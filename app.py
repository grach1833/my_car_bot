import asyncio
import logging
import sqlite3
import os
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, \
    InlineKeyboardButton, InputMediaPhoto
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ----- НАСТРОЙКИ (через переменные окружения для безопасности) -----
# Для локальной разработки можно загрузить .env, на сервере это проигнорируется
try:
    from dotenv import load_dotenv
    load_dotenv()  # Загружаем переменные из .env только если файл существует
except ImportError:
    pass  # На сервере dotenv может не быть, используем только os.environ

TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', ))
SELLER_USERNAME = os.environ.get('SELLER_USERNAME', "")
SELLER_PHONE = os.environ.get('SELLER_PHONE', "")

# ----- ПРОСТОЙ ПУТЬ К ПАПКЕ -----
PHOTOS_DIR = "car_photos"

# Создаём папку для фото
try:
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR)
        print(f"✅ Создана папка: {PHOTOS_DIR}")
    else:
        print(f"✅ Папка уже существует: {PHOTOS_DIR}")

    # Проверяем права на запись
    test_file = os.path.join(PHOTOS_DIR, "test.txt")
    with open(test_file, "w") as f:
        f.write("test")
    os.remove(test_file)
    print(f"✅ Папка доступна для записи")
except Exception as e:
    print(f"❌ Ошибка с папкой: {e}")

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# ----- БАЗА ДАННЫХ -----
def init_db():
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            model TEXT,
            year INTEGER,
            price INTEGER,
            mileage INTEGER,
            description TEXT,
            photos TEXT
        )
    """)

    cursor.execute("PRAGMA table_info(cars)")
    columns = [column[1] for column in cursor.fetchall()]

    if 'photos' not in columns:
        print("➕ Добавляем колонку photos...")
        cursor.execute("ALTER TABLE cars ADD COLUMN photos TEXT")

    conn.commit()
    conn.close()
    print("✅ База данных готова!")


init_db()


# ----- СОСТОЯНИЯ -----
class AddCarStates(StatesGroup):
    brand = State()
    model = State()
    year = State()
    price = State()
    mileage = State()
    description = State()
    photos = State()


# ----- КЛАВИАТУРЫ -----
def get_main_keyboard():
    buttons = [[KeyboardButton(text="📋 Каталог")], [KeyboardButton(text="📞 Связаться с продавцом")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_admin_keyboard():
    buttons = [
        [KeyboardButton(text="➕ Добавить машину")],
        [KeyboardButton(text="🗑 Удалить машину")],
        [KeyboardButton(text="📋 Мои объявления")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_back_keyboard():
    buttons = [[KeyboardButton(text="◀️ Назад")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_photo_keyboard():
    buttons = [[KeyboardButton(text="✅ Готово")], [KeyboardButton(text="◀️ Назад")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_contact_keyboard():
    buttons = [
        [InlineKeyboardButton(text="💬 Написать в Telegram", url=f"https://t.me/{SELLER_USERNAME}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ----- ФУНКЦИИ БАЗЫ ДАННЫХ -----
def add_car_to_db(brand, model, year, price, mileage, description, photos_str):
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM cars ORDER BY id")
    existing_ids = [row[0] for row in cursor.fetchall()]

    free_id = 1
    while free_id in existing_ids:
        free_id += 1

    cursor.execute("""
        INSERT INTO cars (id, brand, model, year, price, mileage, description, photos)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (free_id, brand, model, year, price, mileage, description, photos_str))

    conn.commit()
    conn.close()
    return free_id


def get_all_cars():
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, brand, model, year, price, mileage FROM cars ORDER BY id")
    cars = cursor.fetchall()
    conn.close()
    return cars


def get_car_by_id(car_id):
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cars WHERE id = ?", (car_id,))
    car = cursor.fetchone()
    conn.close()
    return car


def delete_car_by_id(car_id):
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()
    cursor.execute("SELECT photos FROM cars WHERE id = ?", (car_id,))
    result = cursor.fetchone()
    if result and result[0]:
        photo_paths = result[0].split(',')
        for photo_path in photo_paths:
            full_path = os.path.join(PHOTOS_DIR, photo_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                print(f"🗑 Удалено фото: {full_path}")
    cursor.execute("DELETE FROM cars WHERE id = ?", (car_id,))
    conn.commit()
    conn.close()


def reassign_ids():
    conn = sqlite3.connect("cars.db")
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM cars ORDER BY id")
    cars = cursor.fetchall()

    if not cars:
        conn.close()
        return

    cursor.execute("CREATE TABLE cars_temp AS SELECT * FROM cars WHERE 0")

    for new_id, car in enumerate(cars, start=1):
        cursor.execute("""
            INSERT INTO cars_temp (id, brand, model, year, price, mileage, description, photos)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (new_id, car[1], car[2], car[3], car[4], car[5], car[6], car[7]))

    cursor.execute("DROP TABLE cars")
    cursor.execute("ALTER TABLE cars_temp RENAME TO cars")

    conn.commit()
    conn.close()


# ----- ОБРАБОТЧИКИ -----
@dp.message(Command("start"))
async def start(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🚗 Добро пожаловать, *администратор*!", parse_mode="Markdown",
                             reply_markup=get_admin_keyboard())
    else:
        await message.answer("🚗 Добро пожаловать в автосалон!", reply_markup=get_main_keyboard())


# ---- АДМИН: ДОБАВЛЕНИЕ МАШИНЫ ----
@dp.message(F.text == "➕ Добавить машину")
async def add_car_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.set_state(AddCarStates.brand)
    await message.answer("🚗 Введите *марку* автомобиля (например: Toyota):",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.brand)
async def process_brand(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.clear()
        await message.answer("❌ Добавление отменено.", reply_markup=get_admin_keyboard())
        return

    await state.update_data(brand=message.text)
    await state.set_state(AddCarStates.model)
    await message.answer("📝 Введите *модель* (например: Camry):",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.model)
async def process_model(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddCarStates.brand)
        await message.answer("🔙 Возврат к марке. Введите *марку* автомобиля:",
                             parse_mode="Markdown",
                             reply_markup=get_back_keyboard())
        return

    await state.update_data(model=message.text)
    await state.set_state(AddCarStates.year)
    await message.answer("📅 Введите *год выпуска* (цифрами, например: 2021):",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.year)
async def process_year(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddCarStates.model)
        await message.answer("🔙 Возврат к модели. Введите *модель* автомобиля:",
                             parse_mode="Markdown",
                             reply_markup=get_back_keyboard())
        return

    if not message.text.isdigit():
        await message.answer("❌ Год должен быть числом. Попробуйте снова:")
        return
    await state.update_data(year=int(message.text))
    await state.set_state(AddCarStates.price)
    await message.answer("💰 Введите *цену* в рублях (только цифры):",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.price)
async def process_price(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddCarStates.year)
        await message.answer("🔙 Возврат к году. Введите *год выпуска*:",
                             parse_mode="Markdown",
                             reply_markup=get_back_keyboard())
        return

    if not message.text.isdigit():
        await message.answer("❌ Цена должна быть числом. Попробуйте снова:")
        return
    await state.update_data(price=int(message.text))
    await state.set_state(AddCarStates.mileage)
    await message.answer("📊 Введите *пробег* в километрах (только цифры):",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.mileage)
async def process_mileage(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddCarStates.price)
        await message.answer("🔙 Возврат к цене. Введите *цену* автомобиля:",
                             parse_mode="Markdown",
                             reply_markup=get_back_keyboard())
        return

    if not message.text.isdigit():
        await message.answer("❌ Пробег должен быть числом. Попробуйте снова:")
        return
    await state.update_data(mileage=int(message.text))
    await state.set_state(AddCarStates.description)
    await message.answer("📝 Введите *описание* автомобиля:",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.description)
async def process_description(message: Message, state: FSMContext):
    if message.text == "◀️ Назад":
        await state.set_state(AddCarStates.mileage)
        await message.answer("🔙 Возврат к пробегу. Введите *пробег* автомобиля:",
                             parse_mode="Markdown",
                             reply_markup=get_back_keyboard())
        return

    await state.update_data(description=message.text)
    await state.set_state(AddCarStates.photos)
    await message.answer(
        "📸 Отправьте *фото* автомобиля.\nМаксимум 5 фото.\nКогда закончите, нажмите кнопку **✅ Готово**",
        parse_mode="Markdown",
        reply_markup=get_photo_keyboard()
    )


@dp.message(AddCarStates.photos, F.photo)
async def process_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])

    if len(photos) >= 5:
        await message.answer("❌ Вы отправили уже 5 фото. Нажмите **✅ Готово**", parse_mode="Markdown")
        return

    try:
        best_photo = message.photo[-1]
        file = await bot.get_file(best_photo.file_id)

        timestamp = int(time.time() * 1000)
        filename = f"{message.from_user.id}_{timestamp}_{len(photos)}.jpg"
        file_path = os.path.join(PHOTOS_DIR, filename)

        print(f"📸 Сохраняем фото в: {file_path}")
        await bot.download_file(file.file_path, file_path)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
            print(f"✅ Фото сохранено! Размер: {os.path.getsize(file_path)} байт")
            photos.append(filename)
            await state.update_data(photos=photos)
        else:
            print(f"❌ Ошибка: файл не создан")
            await message.answer("❌ Не удалось сохранить фото. Попробуйте ещё раз.")
            return

    except Exception as e:
        print(f"❌ Ошибка при сохранении: {e}")
        await message.answer(f"❌ Ошибка: {e}")
        return

    remaining = 5 - len(photos)
    await message.answer(f"✅ Фото добавлено! ({len(photos)}/5). Осталось: {remaining}. Нажмите **✅ Готово**",
                         parse_mode="Markdown")


@dp.message(AddCarStates.photos, F.text == "✅ Готово")
async def finish_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])

    if len(photos) == 0:
        await message.answer("❌ Отправьте хотя бы одно фото, затем нажмите 'Готово'")
        return

    photos_str = ','.join(photos)
    car_id = add_car_to_db(
        data['brand'], data['model'], data['year'],
        data['price'], data['mileage'], data['description'],
        photos_str
    )

    print(f"✅ Машина ID:{car_id} добавлена. Фото: {photos_str}")

    await state.clear()
    await message.answer(f"✅ Автомобиль добавлен! ID: {car_id}", reply_markup=get_admin_keyboard())


@dp.message(AddCarStates.photos, F.text == "◀️ Назад")
async def back_from_photos(message: Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get('photos', [])
    for filename in photos:
        file_path = os.path.join(PHOTOS_DIR, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"🗑 Удалено фото: {file_path}")

    await state.set_state(AddCarStates.description)
    await message.answer("🔙 Возврат к описанию. Введите *описание* автомобиля:",
                         parse_mode="Markdown",
                         reply_markup=get_back_keyboard())


@dp.message(AddCarStates.photos)
async def photo_waiting_message(message: Message):
    if message.text in ["✅ Готово", "◀️ Назад"]:
        return
    await message.answer("❌ Отправьте ФОТО или нажмите **✅ Готово** или **◀️ Назад**", parse_mode="Markdown")


# ---- АДМИН: УДАЛЕНИЕ ----
@dp.message(F.text == "🗑 Удалить машину")
async def delete_car_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cars = get_all_cars()
    if not cars:
        await message.answer("📭 Нет машин для удаления")
        return
    text = "🗑 Введите ID машины для удаления:\n\n"
    for car_id, brand, model, year, price, mileage in cars:
        text += f"ID {car_id}: {brand} {model}, {year} г.\n"
    await message.answer(text)


@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text and msg.text.isdigit())
async def delete_car_process(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    car_id = int(message.text)
    car = get_car_by_id(car_id)
    if not car:
        await message.answer("❌ Машина с таким ID не найдена")
        return
    delete_car_by_id(car_id)
    reassign_ids()
    await message.answer(f"✅ Машина с ID {car_id} удалена! ID перенумерованы.", reply_markup=get_admin_keyboard())


# ---- АДМИН: МОИ ОБЪЯВЛЕНИЯ ----
@dp.message(F.text == "📋 Мои объявления")
async def my_cars(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    cars = get_all_cars()
    if not cars:
        await message.answer("📭 Нет объявлений")
        return
    text = "🔧 *Ваши объявления:*\n\n"
    for car_id, brand, model, year, price, mileage in cars:
        text += f"ID {car_id}: {brand} {model}, {year} г. - {price:,} ₽\n"
    await message.answer(text, parse_mode="Markdown")


# ---- ПОКУПАТЕЛИ: КАТАЛОГ ----
@dp.message(F.text == "📋 Каталог")
async def catalog(message: Message):
    cars = get_all_cars()
    if not cars:
        await message.answer("🚫 В каталоге пока нет машин.")
        return
    text = "📋 *Наши автомобили:*\n\n"
    for car_id, brand, model, year, price, mileage in cars:
        text += f"*{car_id}.* {brand} {model}, {year} г.\n   💰 {price:,} ₽ | {mileage:,} км\n\n"
    await message.answer(text, parse_mode="Markdown")
    await message.answer("💡 Отправьте ID машины для подробностей")


# ---- ПОКУПАТЕЛИ: СВЯЗАТЬСЯ С ПРОДАВЦОМ ----
@dp.message(F.text == "📞 Связаться с продавцом")
async def contact(message: Message):
    await message.answer(
        f"📞 *Свяжитесь с продавцом:*\n\n"
        f"📱 *Телефон:* `{SELLER_PHONE}`\n"
        f"✈️ *Telegram:* @{SELLER_USERNAME}",
        parse_mode="Markdown",
        reply_markup=get_contact_keyboard()
    )


# ---- ПОКУПАТЕЛИ: ДЕТАЛИ ПО ID ----
@dp.message(lambda msg: msg.text and msg.text.isdigit())
async def car_details(message: Message, state: FSMContext):
    if await state.get_state() is not None:
        return

    car_id = int(message.text)
    car = get_car_by_id(car_id)

    if not car:
        await message.answer("❌ Машина не найдена")
        return

    id = car[0]
    brand = car[1]
    model = car[2]
    year = car[3]
    price = car[4]
    mileage = car[5]
    description = car[6]
    photos_str = car[7]

    text = f"🚘 *{brand} {model}*, {year} г.\n💰 *Цена:* {price:,} ₽\n📊 *Пробег:* {mileage:,} км\n\n📝 *Описание:* {description}\n\n🔑 ID: {id}"

    if photos_str:
        photo_filenames = photos_str.split(',')
        existing_photos = []

        print(f"🔍 Ищем фото для машины {car_id}: {photo_filenames}")

        for filename in photo_filenames:
            file_path = os.path.join(PHOTOS_DIR, filename)
            if os.path.exists(file_path):
                existing_photos.append(file_path)
                print(f"✅ Найдено: {file_path}")
            else:
                print(f"❌ Не найдено: {file_path}")

        if existing_photos:
            media_group = []
            for photo_path in existing_photos:
                media_group.append(InputMediaPhoto(media=FSInputFile(photo_path)))

            if media_group:
                media_group[0].caption = text
                media_group[0].parse_mode = "Markdown"
                await message.answer_media_group(media=media_group)
        else:
            await message.answer(f"{text}\n\n⚠️ Фото не загружены", parse_mode="Markdown")
    else:
        await message.answer(text, parse_mode="Markdown")


# ---- ЗАПУСК ----
async def main():
    logging.basicConfig(level=logging.INFO)
    print("=" * 50)
    print("🚀 Бот запускается...")
    print(f"👑 Admin ID: {ADMIN_ID}")
    print(f"📁 Текущая директория: {os.getcwd()}")
    print(f"📁 Папка с фото: {PHOTOS_DIR}")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")