import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMemberUpdated
)
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
import aiosqlite

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ROOT_ADMIN_ID = int(os.getenv("ROOT_ADMIN_ID"))

SEND_DELAY = 3.0  # защита от бана
DB_NAME = "bot.db"
# ============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ================== DB ==================
async def db_init():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS stats (
            sent INTEGER,
            errors INTEGER
        );
        """)
        cur = await db.execute("SELECT COUNT(*) FROM stats")
        if (await cur.fetchone())[0] == 0:
            await db.execute("INSERT INTO stats VALUES (0,0)")
        await db.execute("INSERT OR IGNORE INTO admins VALUES (?)", (ROOT_ADMIN_ID,))
        await db.commit()

# ================== HELPERS ==================
async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        )
        return await cur.fetchone() is not None

async def get_groups():
    async with aiosqlite.connect(DB_NAME) as db:
        cur = await db.execute("SELECT chat_id, title FROM groups")
        return await cur.fetchall()

async def add_group(chat_id, title):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups VALUES (?,?)", (chat_id, title)
        )
        await db.commit()

async def remove_group(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM groups WHERE chat_id=?", (chat_id,))
        await db.commit()

async def stat_inc(field):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE stats SET {field}={field}+1")
        await db.commit()

# ================== FSM ==================
class Send(StatesGroup):
    content = State()
    schedule = State()

# ================== KEYBOARDS ==================
main_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📢 Рассылка", callback_data="send")],
    [InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule")],
    [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
])

time_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Через 5 минут", callback_data="t_5")],
    [InlineKeyboardButton(text="Через 30 минут", callback_data="t_30")],
    [InlineKeyboardButton(text="Через 1 час", callback_data="t_60")],
])

# ================== START ==================
@dp.message(Command("start"))
async def start(message: Message):
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return
    await message.answer("✅ Панель управления", reply_markup=main_kb)

# ================== GROUP TRACK ==================
@dp.chat_member()
async def track_groups(event: ChatMemberUpdated):
    if event.new_chat_member.status in ("member", "administrator"):
        await add_group(event.chat.id, event.chat.title)

# ================== STATS ==================
@dp.callback_query(F.data == "stats")
async def stats(call: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        s = await db.execute("SELECT sent, errors FROM stats")
        sent, errors = await s.fetchone()
        g = await db.execute("SELECT COUNT(*) FROM groups")
        groups = (await g.fetchone())[0]

    await call.message.answer(
        f"📊 Статистика\n\n"
        f"Групп: {groups}\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {errors}"
    )

# ================== SEND ==================
@dp.callback_query(F.data == "send")
async def send_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("✍️ Отправь текст или медиа")
    await state.set_state(Send.content)

@dp.message(Send.content)
async def get_content(message: Message, state: FSMContext):
    await state.update_data(message=message)
    await message.answer("🚀 Отправляю во все группы…")
    await state.clear()
    await send_to_all(message)

async def send_to_all(message: Message):
    groups = await get_groups()
    for chat_id, _ in groups:
        try:
            if message.photo:
                await bot.send_photo(chat_id, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(chat_id, message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(chat_id, message.document.file_id, caption=message.caption)
            else:
                await bot.send_message(chat_id, message.text)

            await stat_inc("sent")
            await asyncio.sleep(SEND_DELAY)

        except (TelegramForbiddenError, TelegramBadRequest):
            await stat_inc("errors")
            await remove_group(chat_id)

# ================== SCHEDULE ==================
@dp.callback_query(F.data == "schedule")
async def schedule_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("✍️ Отправь сообщение для отложки")
    await state.set_state(Send.content)

@dp.message(Send.content)
async def schedule_get_msg(message: Message, state: FSMContext):
    await state.update_data(message=message)
    await message.answer("⏰ Выбери время", reply_markup=time_kb)
    await state.set_state(Send.schedule)

@dp.callback_query(Send.schedule)
async def schedule_time(call: CallbackQuery, state: FSMContext):
    mins = int(call.data.split("_")[1])
    data = await state.get_data()
    await state.clear()

    async def delayed():
        await asyncio.sleep(mins * 60)
        await send_to_all(data["message"])

    asyncio.create_task(delayed())
    await call.message.answer(f"✅ Запланировано через {mins} минут")

# ================== MAIN ==================
async def main():
    await db_init()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
