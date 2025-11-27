# main.py
import os
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message as TgMessage,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from models import init_db, SessionLocal, User, Message as DbMessage

# --- ИНИЦИАЛИЗАЦИЯ БД И ПАПКИ ---
init_db()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# --- FASTAPI ПРИЛОЖЕНИЕ (САЙТ) ---
app = FastAPI(title="FitCal AI")

# раздаём файлы из uploads/
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# ---------- ХЭНДЛЕРЫ БОТА ----------

def save_user(tg_user):
    """Создаём пользователя в БД, если его ещё нет."""
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
async def cmd_start(message: TgMessage):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Зарегистрироваться", callback_data="register")]
        ]
    )
    await message.answer(
        "Привет! Это FitCal AI.\n"
        "Нажми «Зарегистрироваться», а затем отправь текст, фото или голосовое.",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "register")
async def cb_register(cb: CallbackQuery):
    save_user(cb.from_user)
    await cb.message.answer(
        "✅ Вы зарегистрированы!\n"
        "Теперь просто отправьте текст, фото или голосовое сообщение."
    )
    await cb.answer()


@dp.message(F.text)
async def handle_text(message: TgMessage):
    save_user(message.from_user)
    with SessionLocal() as db:
        db_msg = DbMessage(
            user_id=message.from_user.id,
            type="text",
            text=message.text,
        )
        db.add(db_msg)
        db.commit()
    await message.answer("Текст сохранён ✅ (он появится на сайте).")


@dp.message(F.photo)
async def handle_photo(message: TgMessage):
    save_user(message.from_user)
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    filename = f"photo_{message.from_user.id}_{file.file_unique_id}.jpg"
    dest = UPLOAD_DIR / filename
    await bot.download_file(file.file_path, destination=dest)

    with SessionLocal() as db:
        db_msg = DbMessage(
            user_id=message.from_user.id,
            type="photo",
            file_path=filename,
        )
        db.add(db_msg)
        db.commit()

    await message.answer("Фото сохранено 📸 (будет видно на сайте).")


@dp.message(F.voice)
async def handle_voice(message: TgMessage):
    save_user(message.from_user)
    voice = message.voice
    file = await bot.get_file(voice.file_id)

    filename = f"voice_{message.from_user.id}_{file.file_unique_id}.ogg"
    dest = UPLOAD_DIR / filename
    await bot.download_file(file.file_path, destination=dest)

    with SessionLocal() as db:
        db_msg = DbMessage(
            user_id=message.from_user.id,
            type="voice",
            file_path=filename,
        )
        db.add(db_msg)
        db.commit()

    await message.answer("Голосовое сохранено 🎙 (будет доступно на сайте).")


# ---------- СТАРТ БОТА В ФОНЕ ПРИ ЗАПУСКЕ САЙТА ----------

async def _start_bot():
    await dp.start_polling(bot)


@app.on_event("startup")
async def on_startup():
    # запускаем бота как фоновой таск внутри uvicorn
    asyncio.create_task(_start_bot())


# ---------- РОУТ САЙТА ----------

@app.get("/", response_class=HTMLResponse)
def index():
    db = SessionLocal()
    rows = (
        db.query(DbMessage, User)
        .join(User, User.id == DbMessage.user_id)
        .order_by(DbMessage.created_at.desc())
        .all()
    )

    html_parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>FitCal AI — сообщения пользователей</title>",
        "<style>",
        "body{font-family:-apple-system,system-ui,Roboto,Arial,sans-serif;"
        "margin:0;padding:20px;background:#f5f5fb;color:#111;}",
        "h1{margin-bottom:16px;}",
        ".grid{display:flex;flex-direction:column;gap:12px;max-width:800px;margin:0 auto;}",
        ".card{background:#fff;border-radius:14px;padding:14px 16px;"
        "box-shadow:0 4px 12px rgba(0,0,0,0.04);}",
        ".meta{font-size:13px;color:#777;margin-bottom:4px;}",
        ".type{font-size:12px;color:#555;margin-bottom:8px;text-transform:uppercase;}",
        "img{max-width:100%;border-radius:10px;margin-top:6px;}",
        "audio{width:100%;margin-top:6px;}",
        "</style></head><body>",
        "<h1>Сообщения пользователей FitCal AI</h1>",
        "<div class='grid'>",
    ]

    if not rows:
        html_parts.append("<p>Пока нет ни одного сообщения. Отправь что-нибудь боту 😉</p>")
    else:
        for msg, user in rows:
            user_name = user.first_name or user.username or f"id {user.id}"
            created = msg.created_at.strftime("%d.%m.%Y %H:%M")
            html_parts.append("<div class='card'>")
            html_parts.append(f"<div class='meta'>{user_name} • {created}</div>")
            html_parts.append(f"<div class='type'>Тип: {msg.type}</div>")

            if msg.type == "text":
                html_parts.append(f"<div>{msg.text}</div>")
            elif msg.type == "photo":
                html_parts.append(
                    f"<img src='/uploads/{msg.file_path}' alt='photo from {user_name}'>"
                )
            elif msg.type == "voice":
                html_parts.append(
                    f"<audio controls src='/uploads/{msg.file_path}'></audio>"
                )

            html_parts.append("</div>")  # card

    html_parts.append("</div></body></html>")
    return "".join(html_parts)
