import math
import asyncio
import os
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from flask import Flask
from threading import Thread
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Загрузка .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Настройка бота и диспетчера
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# Состояния
class Form(StatesGroup):
    waiting_for_category = State()
    waiting_for_price = State()
    waiting_for_delivery_type = State()
    waiting_for_tracking_code = State()

# Клавиатуры
new_calc_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔁 Новый расчёт")]],
    resize_keyboard=True
)

start_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛒 Новый расчёт")],
        [KeyboardButton(text="📦 Отследить заказ")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

# Получение курса юаня
def get_cbr_exchange_rate():
    try:
        response = requests.get("https://www.cbr.ru/scripts/XML_daily.asp")
        response.encoding = "windows-1251"
        tree = ET.fromstring(response.text)
        for valute in tree.findall("Valute"):
            if valute.find("CharCode").text == "CNY":
                value = valute.find("Value").text.replace(",", ".")
                nominal = int(valute.find("Nominal").text)
                return float(value) / nominal
    except Exception as e:
        print(f"Ошибка при получении курса ЦБ: {e}")
        return 11.5

# Чтение статуса из Google Sheets
def get_order_status(order_code):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
        client = gspread.authorize(creds)

        sheet = client.open("Заказы POIZON").sheet1
        records = sheet.get_all_records()

        for row in records:
            if row["Код заказа"].strip().lower() == order_code.lower():
                return row["Статус"]
        return None
    except Exception as e:
        print(f"Ошибка при чтении таблицы: {e}")
        return None

# Хэндлер /start
@dp.message(F.text == "/start")
async def start_handler(message: Message, state: FSMContext):
    await message.answer(
        "Здравствуйте! Я помогу вам рассчитать стоимость товара с доставкой или отследить заказ.",
        reply_markup=start_keyboard
    )
    await state.clear()

@dp.message(F.text == "🔁 Новый расчёт")
@dp.message(F.text == "🛒 Новый расчёт")
async def restart_handler(message: Message, state: FSMContext):
    await message.answer(
        "Выберите категорию товара:"
        "1. Обувь 👟"
        "2. Футболка/штаны/худи 👕"
        "3. Другое ❓"
        "Выберите номер категории (1, 2 или 3):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="1")],
                [KeyboardButton(text="2")],
                [KeyboardButton(text="3")],
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )
    await state.set_state(Form.waiting_for_category)

@dp.message(F.text == "📦 Отследить заказ")
async def track_order_start(message: Message, state: FSMContext):
    await message.answer("Введите код вашего заказа (например: @vasya_1):")
    await state.set_state(Form.waiting_for_tracking_code)

@dp.message(Form.waiting_for_tracking_code)
async def handle_tracking_code(message: Message, state: FSMContext):
    code = message.text.strip()
    status = get_order_status(code)
    if status:
       await message.answer(f"Статус вашего заказа:\n<b>{status}</b>", parse_mode="HTML", reply_markup=new_calc_keyboard)

    else:
        await message.answer("Код не найден. Проверьте правильность и попробуйте снова.", reply_markup=new_calc_keyboard)
    await state.clear()

@dp.message(Form.waiting_for_category)
async def category_handler(message: Message, state: FSMContext):
    category = message.text.strip()
    if category not in ["1", "2", "3"]:
        await message.answer("Пожалуйста, выберите 1, 2 или 3.")
        return
    if category == "3":
        await message.answer("Свяжитесь с менеджером: @the_poiz_adm", reply_markup=new_calc_keyboard)
        await state.clear()
        return
    await state.update_data(category=category)
    await message.answer("Введите цену товара в юанях ¥:", reply_markup=None)
    await state.set_state(Form.waiting_for_price)

@dp.message(Form.waiting_for_price)
async def price_handler(message: Message, state: FSMContext):
    try:
        price_yuan = float(message.text.strip())
    except ValueError:
        await message.answer("Введите число, например: 289")
        return
    await state.update_data(price_yuan=price_yuan)

    delivery_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Авто 🚚")],
            [KeyboardButton(text="Авиа ✈️")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer("Выберите способ доставки:", reply_markup=delivery_keyboard)
    await state.set_state(Form.waiting_for_delivery_type)

@dp.message(Form.waiting_for_delivery_type)
async def delivery_type_handler(message: Message, state: FSMContext):
    delivery_type = message.text.strip()
    if delivery_type not in ["Авто 🚚", "Авиа ✈️"]:
        await message.answer("Пожалуйста, выберите 'Авто 🚚' или 'Авиа ✈️'")
        return

    data = await state.get_data()
    category = data["category"]
    price_yuan = data["price_yuan"]
    weight = 1.5 if category == "1" else 0.6
    delivery_rate = 800 if delivery_type == "Авто 🚚" else 1900

    cbr_rate = get_cbr_exchange_rate()
    rate = cbr_rate * 1.09
    item_price_rub = price_yuan * rate
    delivery_cost = weight * delivery_rate
    commission = item_price_rub * 0.10
    total_item_price = math.ceil(item_price_rub + commission)
    total_cost = math.ceil(item_price_rub + delivery_cost + commission)

    await message.answer(
        f"<b>Расчёт стоимости:</b>"
        f"Курс юаня: {rate:.2f} ₽"
        f"Способ доставки: {delivery_type}"
        f"Стоимость товара с учётом комиссии (10%): {total_item_price} ₽"
        f"Стоимость доставки из Китая: {math.ceil(delivery_cost)} ₽"
        f"<b>Итого:</b> {total_cost} ₽"
        "Стоимость доставки по РФ (СДЭК, Почта, Boxberry) будет рассчитана нашим менеджером при заказе."
        "Для оформления заказа напишите @the_poiz_adm.",
        reply_markup=new_calc_keyboard
    )
    await state.clear()

# Удаление вебхука и запуск
async def delete_webhook_and_run():
    try:
        await bot.delete_webhook()
        print("Вебхук успешно удалён!")
    except Exception as e:
        print(f"Не удалось удалить вебхук: {e}")
    await dp.start_polling(bot, skip_updates=True)

def start_bot():
    print("Запуск бота через long polling...")
    asyncio.run(delete_webhook_and_run())

# Flask-заглушка
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    Thread(target=run_flask).start()
    start_bot()
