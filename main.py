import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ROOT_ADMIN_ID = int(os.getenv("ROOT_ADMIN_ID"))
DEFAULT_DELAY = 2.0  # оптимальный
# ============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================== DATABASE ==================
db = sqlite3.connect("bot.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    delay REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS stats (
    sent INTEGER
)
""")

cursor.execute("INSERT OR IGNORE INTO stats VALUES (0)")
cursor.execute(
    "INSERT OR IGNORE INTO users VALUES (?, ?)",
    (ROOT_ADMIN_ID, DEFAULT_DELAY)
)
db.commit()

# ================== HELPERS ==================
def log_action(uid, action, group="-"):
    cursor.execute(
        "INSERT INTO logs (user_id, action, group_title, created_at) VALUES (?, ?, ?, ?)",
        (uid, action, group, datetime.utcnow().isoformat())
    )
    db.commit()

def is_allowed(uid):
    cursor.execute("SELECT 1 FROM users WHERE user_id=?", (uid,))
    return cursor.fetchone() is not None

def add_user(uid):
    cursor.execute(
        "INSERT OR IGNORE INTO users VALUES (?, ?)",
        (uid, DEFAULT_DELAY)
    )
    db.commit()

def remove_user(uid):
    cursor.execute("DELETE FROM users WHERE user_id=?", (uid,))
    db.commit()

def get_users():
    cursor.execute("SELECT user_id, delay FROM users")
    return cursor.fetchall()

def get_delay(uid):
    cursor.execute("SELECT delay FROM users WHERE user_id=?", (uid,))
    row = cursor.fetchone()
    return row[0] if row else DEFAULT_DELAY

def set_delay(uid, delay):
    cursor.execute("UPDATE users SET delay=? WHERE user_id=?", (delay, uid))
    db.commit()

def add_group(cid, title):
    cursor.execute(
        "INSERT OR IGNORE INTO groups VALUES (?, ?)",
        (cid, title)
    )
    db.commit()

def get_groups():
    cursor.execute("SELECT chat_id, title FROM groups")
    return cursor.fetchall()

def inc_stats(n):
    cursor.execute("UPDATE stats SET sent = sent + ?", (n,))
    db.commit()

# ================== FSM ==================
class SendFSM(StatesGroup):
    content = State()
    groups = State()
    count = State()

class AdminFSM(StatesGroup):
    add_user = State()
    set_delay = State()

# ================== KEYBOARDS ==================
def start_kb(uid):
    kb = [
        [InlineKeyboardButton(text="📢 Начать рассылку", callback_data="send")],
        [InlineKeyboardButton(text="⏱ Установить задержку", callback_data="set_delay")]
    ]
    if uid == ROOT_ADMIN_ID:
        kb += [
            [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="add_user")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="list_users")],
            [InlineKeyboardButton(text="🧾 Логи действий", callback_data="logs")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

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
    kb.append([InlineKeyboardButton(text="▶️ Отправить", callback_data="go")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================== START ==================
@dp.message(Command("start"))
async def start(message: Message):
    if not is_allowed(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return
    d = get_delay(message.from_user.id)
    await message.answer(
        f"✅ Бот готов\n⏱ Текущий delay: {d} сек (по умолчанию {DEFAULT_DELAY})",
        reply_markup=start_kb(message.from_user.id)
    )

# ================== SET DELAY ==================
@dp.callback_query(F.data == "set_delay")
async def set_delay_btn(call: CallbackQuery, state: FSMContext):
    await call.message.answer(
        f"Введи задержку в секундах\n"
        f"Рекомендуемое значение: {DEFAULT_DELAY} (оптимально)"
    )
    await state.set_state(AdminFSM.set_delay)

@dp.message(AdminFSM.set_delay)
async def save_delay(message: Message, state: FSMContext):
    delay = float(message.text)
    set_delay(message.from_user.id, delay)
    log_action(message.from_user.id, f"Установил delay {delay}")
    await message.answer(f"✅ Delay установлен: {delay} сек")
    await state.clear()

# ================== ADMIN ==================
@dp.callback_query(F.data == "add_user")
async def add_user_btn(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ROOT_ADMIN_ID:
        return
    await call.message.answer("Введи Telegram ID пользователя:")
    await state.set_state(AdminFSM.add_user)

@dp.message(AdminFSM.add_user)
async def add_user_process(message: Message, state: FSMContext):
    uid = int(message.text)
    add_user(uid)
    log_action(message.from_user.id, f"Добавил пользователя {uid}")
    await message.answer("✅ Пользователь добавлен")
    await state.clear()

@dp.callback_query(F.data == "list_users")
async def list_users(call: CallbackQuery):
    users = get_users()
    text = "👥 Пользователи:\n"
    for u, d in users:
        text += f"{u} — delay {d}\n"
    await call.message.answer(text)

@dp.callback_query(F.data == "logs")
async def show_logs(call: CallbackQuery):
    cursor.execute(
        "SELECT user_id, action, group_title, created_at FROM logs ORDER BY id DESC LIMIT 20"
    )
    rows = cursor.fetchall()
    text = "🧾 Логи действий:\n\n"
    for u, a, g, t in rows:
        text += f"{t}\n👤 {u}\n➡ {a}\n📌 {g}\n\n"
    await call.message.answer(text)

# ================== GROUP TRACKING ==================
@dp.my_chat_member()
async def bot_added(event):
    if event.new_chat_member.status in ("member", "administrator"):
        add_group(event.chat.id, event.chat.title)

# ================== MAILING ==================
@dp.callback_query(F.data == "send")
async def start_send(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Пришли сообщение для рассылки:")
    await state.set_state(SendFSM.content)

@dp.message(SendFSM.content)
async def get_content(message: Message, state: FSMContext):
    await state.update_data(
        text=message.text,
        groups=set(),
        user_id=message.from_user.id
    )
    await message.answer("Выбери группы:", reply_markup=groups_kb(set()))
    await state.set_state(SendFSM.groups)

@dp.callback_query(SendFSM.groups)
async def choose_groups(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data["groups"])

    if call.data.startswith("grp_"):
        gid = int(call.data.split("_")[1])
        selected ^= {gid}
        await state.update_data(groups=selected)
        await call.message.edit_reply_markup(groups_kb(selected))

    elif call.data == "go":
        await call.message.answer("Сколько раз отправить сообщение?")
        await state.set_state(SendFSM.count)

@dp.message(SendFSM.count)
async def do_send(message: Message, state: FSMContext):
    count = int(message.text)
    data = await state.get_data()
    delay = get_delay(data["user_id"])

    sent = 0
    for _ in range(count):
        for gid, title in get_groups():
            if gid in data["groups"]:
                await bot.send_message(gid, data["text"])
                log_action(data["user_id"], "Рассылка", title)
                sent += 1
                await asyncio.sleep(delay)

    inc_stats(sent)
    await message.answer("✅ Рассылка завершена")
    await state.clear()

# ================== RUN ==================
async def main():
    print(">>> BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
