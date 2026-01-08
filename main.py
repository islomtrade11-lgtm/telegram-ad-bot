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

cursor.execute("""
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT,
    group_title TEXT,
    created_at TEXT
)
""")

cursor.execute("INSERT OR IGNORE INTO stats VALUES (0, '')")
cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (ROOT_ADMIN_ID,))
db.commit()

# ================= УТИЛИТЫ =================
def log_action(user_id, action, group_title="-"):
    cursor.execute(
        "INSERT INTO logs (user_id, action, group_title, created_at) VALUES (?, ?, ?, ?)",
        (user_id, action, group_title, datetime.utcnow().isoformat())
    )
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


def remove_user(user_id):
    cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))
    db.commit()


def get_users():
    cursor.execute("SELECT user_id FROM users")
    return [u[0] for u in cursor.fetchall()]


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

class Admin(StatesGroup):
    add_user = State()
    remove_user = State()

# ================= КНОПКИ =================
def groups_kb(selected):
    kb = []
    for cid, title in get_groups():
        mark = "✅" if cid in selected else "⬜"
        kb.append([
            InlineKeyboardButton(
                text=f"{mark} {title}",
                callback_data=f"grp_{cid}"
            )
        ])
    kb.append([
        InlineKeyboardButton(text="▶️ Отправить", callback_data="go"),
        InlineKeyboardButton(text="⏰ Отложить", callback_data="delay"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Начать рассылку", callback_data="send")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="add_user")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="list_users")],
        [InlineKeyboardButton(text="🧾 Логи действий", callback_data="logs")]
    ])

def user_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Начать рассылку", callback_data="send")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])

# ================= START =================
@dp.message(Command("start"))
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    kb = admin_kb() if message.from_user.id == ROOT_ADMIN_ID else user_kb()
    await message.answer("✅ Бот готов", reply_markup=kb)

# ================= АДМИНКА =================
@dp.callback_query(F.data == "add_user")
async def add_user_btn(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ROOT_ADMIN_ID:
        return
    await call.message.answer("Введите Telegram ID пользователя:")
    await state.set_state(Admin.add_user)

@dp.message(Admin.add_user)
async def add_user_process(message: Message, state: FSMContext):
    uid = int(message.text)
    add_user(uid)
    log_action(message.from_user.id, f"Добавил пользователя {uid}")
    await message.answer("✅ Пользователь добавлен")
    await state.clear()

@dp.callback_query(F.data == "list_users")
async def list_users(call: CallbackQuery):
    if call.from_user.id != ROOT_ADMIN_ID:
        return
    users = get_users()
    text = "👥 Пользователи:\n" + "\n".join(map(str, users))
    await call.message.answer(text)

@dp.message(Command("del_user"))
async def del_user(message: Message):
    if message.from_user.id != ROOT_ADMIN_ID:
        return
    uid = int(message.text.split()[1])
    remove_user(uid)
    log_action(message.from_user.id, f"Удалил пользователя {uid}")
    await message.answer("❌ Пользователь удалён")

@dp.callback_query(F.data == "logs")
async def show_logs(call: CallbackQuery):
    if call.from_user.id != ROOT_ADMIN_ID:
        return
    cursor.execute(
        "SELECT user_id, action, group_title, created_at FROM logs ORDER BY id DESC LIMIT 20"
    )
    rows = cursor.fetchall()
    text = "🧾 Последние действия:\n\n"
    for u, a, g, t in rows:
        text += f"{t}\n👤 {u}\n➡️ {a}\n📌 {g}\n\n"
    await call.message.answer(text)

# ================= ГРУППЫ =================
@dp.my_chat_member()
async def bot_added(event):
    if event.new_chat_member.status in ("member", "administrator"):
        add_group(event.chat.id, event.chat.title)

# ================= РАССЫЛКА =================
@dp.callback_query(F.data == "send")
async def start_send(call: CallbackQuery, state: FSMContext):
    await call.message.answer("✍️ Пришли текст / фото / видео:")
    await state.set_state(Send.content)

@dp.message(Send.content)
async def get_content(message: Message, state: FSMContext):
    data = {"text": message.text, "photo": None, "video": None}
    if message.photo:
        data["photo"] = message.photo[-1].file_id
        data["text"] = message.caption
    if message.video:
        data["video"] = message.video.file_id
        data["text"] = message.caption

    await state.update_data(**data, groups=set(), user_id=message.from_user.id)
    await message.answer("📌 Выбери группы:", reply_markup=groups_kb(set()))
    await state.set_state(Send.groups)

@dp.callback_query(Send.groups)
async def choose_groups(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data["groups"])

    if call.data.startswith("grp_"):
        gid = int(call.data.split("_")[1])
        selected ^= {gid}
        await state.update_data(groups=selected)
        await call.message.edit_reply_markup(groups_kb(selected))

    elif call.data == "go":
        await call.message.answer("🔁 Сколько раз отправить?")
        await state.set_state(Send.count)

    elif call.data == "delay":
        await call.message.answer("⏰ Через сколько минут отправить?")
        await state.set_state(Send.delay)

@dp.message(Send.delay)
async def set_delay(message: Message, state: FSMContext):
    minutes = int(message.text)
    await state.update_data(send_at=datetime.utcnow() + timedelta(minutes=minutes))
    await message.answer("🔁 Сколько раз отправить?")
    await state.set_state(Send.count)

@dp.message(Send.count)
async def do_send(message: Message, state: FSMContext):
    count = int(message.text)
    data = await state.get_data()

    async def sender():
        sent = 0
        for _ in range(count):
            for gid in data["groups"]:
                title = next(t for i, t in get_groups() if i == gid)
                if data["photo"]:
                    await bot.send_photo(gid, data["photo"], caption=data["text"])
                elif data["video"]:
                    await bot.send_video(gid, data["video"], caption=data["text"])
                else:
                    await bot.send_message(gid, data["text"])
                log_action(data["user_id"], "Рассылка", title)
                sent += 1
                await asyncio.sleep(SEND_DELAY)
        update_stats(sent)

    if "send_at" in data:
        await asyncio.sleep((data["send_at"] - datetime.utcnow()).total_seconds())
    await sender()
    await message.answer("✅ Рассылка завершена")
    await state.clear()

# ================= ЗАПУСК =================
async def main():
    print(">>> BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
