# bot.py

import os
import asyncio

from models import init_db   # <-- ДОБАВЛЕНО!

# Создаём таблицы при старте
init_db()  # <-- ДОБАВЛЕНО!

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from models import SessionLocal, User, Message as DbMessage
from datetime import datetime
from pathlib import Path


BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(BOT_TOKEN)
dp = Dispatcher()
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

def save_user(tg_user):
    with SessionLocal() as db:
        user = db.get(User, tg_user.id)
        if not user:
            user = User(
                id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                created_at=datetime.utcnow(),
            )
            db.add(user)
            db.commit()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Зарегистрироваться", callback_data="register")]
    ])
    await message.answer("Привет! Нажмите, чтобы зарегистрироваться.", reply_markup=kb)

@dp.callback_query(F.data == "register")
async def register(cb: CallbackQuery):
    save_user(cb.from_user)
    await cb.message.answer("Вы зарегистрированы! Теперь отправляйте текст, фото или голос.")
    await cb.answer()

@dp.message(F.text)
async def text_handler(msg: Message):
    save_user(msg.from_user)
    with SessionLocal() as db:
        db.add(DbMessage(user_id=msg.from_user.id, type="text", text=msg.text))
        db.commit()
    await msg.answer("Текст сохранён!")

@dp.message(F.photo)
async def photo_handler(msg: Message):
    save_user(msg.from_user)
    photo = msg.photo[-1]
    file = await bot.get_file(photo.file_id)
    filename = f"photo_{msg.from_user.id}_{file.file_unique_id}.jpg"
    dest = UPLOAD_DIR / filename
    await bot.download_file(file.file_path, destination=dest)

    with SessionLocal() as db:
        db.add(DbMessage(user_id=msg.from_user.id, type="photo", file_path=filename))
        db.commit()
    await msg.answer("Фото сохранено!")

@dp.message(F.voice)
async def voice_handler(msg: Message):
    save_user(msg.from_user)
    voice = msg.voice
    file = await bot.get_file(voice.file_id)
    filename = f"voice_{msg.from_user.id}_{file.file_unique_id}.ogg"
    dest = UPLOAD_DIR / filename
    await bot.download_file(file.file_path, destination=dest)

    with SessionLocal() as db:
        db.add(DbMessage(user_id=msg.from_user.id, type="voice", file_path=filename))
        db.commit()
    await msg.answer("Голосовое сохранено!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
