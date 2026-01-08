import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ROOT_ADMIN_ID = int(os.getenv("ROOT_ADMIN_ID"))
SEND_DELAY = float(os.getenv("SEND_DELAY", 0.5))
# ============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА =================
db = sqlite3.connect("bot.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    sent INTEGER,
    last TEXT
)
""")

cursor.execute("INSERT OR IGNORE INTO stats VALUES (0, '')")
cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (ROOT_ADMIN_ID,))
db.commit()


def add_group(chat_id, title):
    cursor.execute(
        "INSERT OR IGNORE INTO groups VALUES (?, ?)",
        (chat_id, title)
    )
    db.commit()


def get_groups():
    cursor.execute("SELECT chat_id, title FROM groups")
    return cursor.fetchall()


def is_allowed(user_id):
    cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None


def add_user(user_id):
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    db.commit()


def update_stats(count):
    cursor.execute(
        "UPDATE stats SET sent = sent + ?, last = ?",
        (count, datetime.utcnow().isoformat())
    )
    db.commit()


# ================= СОСТОЯНИЯ =================
class Send(StatesGroup):
    content = State()
    groups = State()
    count = State()
    delay = State()


# ================= КНОПКИ =================
def groups_kb(selected: set[int]):
    keyboard = []

    for cid, title in get_groups():
        mark = "✅" if cid in selected else "⬜"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{mark} {title}",
                callback_data=f"grp_{cid}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="▶️ Отправить", callback_data="go"),
        InlineKeyboardButton(text="⏰ Отложить", callback_data="delay"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


start_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📢 Начать рассылку", callback_data="send")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ]
)

# ================= START =================
@dp.message(Command("start"))
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    await message.answer("✅ Бот готов", reply_markup=start_kb)


# ================= ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЕЙ =================
@dp.message(Command("add_user"))
async def cmd_add_user(message: Message):
    if message.from_user.id != ROOT_ADMIN_ID:
        return

    try:
        uid = int(message.text.split()[1])
        add_user(uid)
        await message.answer("✅ Пользователь добавлен")
    except Exception:
        await message.answer("Используй: /add_user <telegram_id>")


# ================= СТАТИСТИКА =================
@dp.callback_query(F.data == "stats")
async def show_stats(call: CallbackQuery):
    cursor.execute("SELECT sent, last FROM stats")
    sent, last = cursor.fetchone()

    await call.message.answer(
        f"📊 Отправлено сообщений: {sent}\n🕒 Последняя рассылка: {last}"
    )


# ================= ДОБАВЛЕНИЕ ГРУПП =================
@dp.my_chat_member()
async def bot_added(event):
    if event.new_chat_member.status in ("member", "administrator"):
        add_group(event.chat.id, event.chat.title)


# ================= РАССЫЛКА =================
@dp.callback_query(F.data == "send")
async def start_send(call: CallbackQuery, state: FSMContext):
    await call.message.answer("✍️ Пришли текст / фото / видео для рассылки:")
    await state.set_state(Send.content)


@dp.message(Send.content)
async def get_content(message: Message, state: FSMContext):
    data = {
        "text": message.text,
        "photo": None,
        "video": None,
    }

    if message.photo:
        data["photo"] = message.photo[-1].file_id
        data["text"] = message.caption

    if message.video:
        data["video"] = message.video.file_id
        data["text"] = message.caption

    await state.update_data(**data, groups=set())
    await message.answer("📌 Выбери группы:", reply_markup=groups_kb(set()))
    await state.set_state(Send.groups)


@dp.callback_query(Send.groups)
async def choose_groups(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected: set[int] = set(data["groups"])

    if call.data.startswith("grp_"):
        gid = int(call.data.split("_")[1])
        selected ^= {gid}
        await state.update_data(groups=selected)
        await call.message.edit_reply_markup(reply_markup=groups_kb(selected))

    elif call.data == "go":
        await call.message.answer("🔁 Сколько раз отправить сообщение?")
        await state.set_state(Send.count)

    elif call.data == "delay":
        await call.message.answer("⏰ Через сколько минут отправить?")
        await state.set_state(Send.delay)


@dp.message(Send.delay)
async def set_delay(message: Message, state: FSMContext):
    minutes = int(message.text)
    await state.update_data(
        send_at=datetime.utcnow() + timedelta(minutes=minutes)
    )
    await message.answer("🔁 Сколько раз отправить сообщение?")
    await state.set_state(Send.count)


@dp.message(Send.count)
async def do_send(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Нужно число")
        return

    count = int(message.text)
    data = await state.get_data()

    async def sender():
        sent = 0
        for _ in range(count):
            for gid in data["groups"]:
                try:
                    if data["photo"]:
                        await bot.send_photo(gid, data["photo"], caption=data["text"])
                    elif data["video"]:
                        await bot.send_video(gid, data["video"], caption=data["text"])
                    else:
                        await bot.send_message(gid, data["text"])

                    sent += 1
                    await asyncio.sleep(SEND_DELAY)
                except Exception as e:
                    logging.error(e)

        update_stats(sent)

    if "send_at" in data:
        delay = (data["send_at"] - datetime.utcnow()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await sender()
    else:
        await sender()

    await message.answer("✅ Рассылка завершена")
    await state.clear()


# ================= ЗАПУСК =================
async def main():
    print(">>> BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
