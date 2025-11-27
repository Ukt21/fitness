import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

from PIL import Image, ImageDraw, ImageFont

# ==== опционально: ИИ для советов ====
try:
    import openai  # нужен, если хочешь реальные советы от ИИ
except ImportError:
    openai = None  # type: ignore

# ==== КОНФИГ ====
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if OPENAI_API_KEY and openai:
    openai.api_key = OPENAI_API_KEY

bot = Bot(token=BOT_TOKEN, parse_mode=types.ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# ==== МОДЕЛИ ДАННЫХ ====
@dataclass
class DailyNorm:
    kcal: int
    protein: int
    fat: int
    carb: int

@dataclass
class UserProfile:
    age: int
    weight: float
    height: int
    sex: str      # "m" или "f"
    activity: str # "1".."4"
    goal: str     # "loss", "keep", "gain"
    daily: DailyNorm

@dataclass
class FoodEntry:
    name: str
    grams: int
    kcal: int
    protein: float
    fat: float
    carb: float

# user_id -> профиль
USERS: Dict[int, UserProfile] = {}
# user_id -> { "YYYY-MM-DD": [FoodEntry, ...] }
FOOD_LOG: Dict[int, Dict[str, List[FoodEntry]]] = {}

# простая БД продуктов на 100 г
PRODUCTS = {
    "курица": {"kcal": 165, "protein": 31, "fat": 3.6, "carb": 0},
    "рис": {"kcal": 340, "protein": 7, "fat": 0.7, "carb": 76},
    "яйцо": {"kcal": 155, "protein": 13, "fat": 11, "carb": 1.1},
    "овсянка": {"kcal": 370, "protein": 13, "fat": 7, "carb": 68},
    "плов": {"kcal": 185, "protein": 6, "fat": 10, "carb": 18},
    "лагман": {"kcal": 145, "protein": 6, "fat": 6, "carb": 17},
    "самса": {"kcal": 290, "protein": 9, "fat": 16, "carb": 26},
    "салат": {"kcal": 40, "protein": 2, "fat": 2, "carb": 4},
    "яблоко": {"kcal": 52, "protein": 0.3, "fat": 0.2, "carb": 14},
    "банан": {"kcal": 89, "protein": 1.1, "fat": 0.3, "carb": 23},
}

# уровни активности
ACTIVITY_LEVELS = {
    "1": 1.2,
    "2": 1.375,
    "3": 1.55,
    "4": 1.725,
}

# ==== СОСТОЯНИЯ ====
class Register(StatesGroup):
    age = State()
    weight = State()
    height = State()
    sex = State()
    activity = State()
    goal = State()

class AddMeal(StatesGroup):
    waiting_input = State()

# ==== КЛАВИАТУРА ====
def main_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        types.KeyboardButton("➕ Приём пищи"),
        types.KeyboardButton("📊 Мой день"),
    )
    kb.add(
        types.KeyboardButton("📈 Прогресс"),
        types.KeyboardButton("💬 Совет от ИИ"),
    )
    kb.add(
        types.KeyboardButton("⚙️ Профиль"),
    )
    return kb

# ==== ХЕЛПЕРЫ ====
def calc_daily_norm(weight: float, height: int, age: int, sex: str, activity: str, goal: str) -> DailyNorm:
    # Миффлин-Сан Жеор
    if sex == "m":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161

    factor = ACTIVITY_LEVELS.get(activity, 1.2)
    kcal = bmr * factor

    if goal == "loss":
        kcal *= 0.85
    elif goal == "gain":
        kcal *= 1.15

    protein = weight * 1.8
    fat = weight * 0.9
    carb = (kcal - (protein * 4 + fat * 9)) / 4

    return DailyNorm(
        kcal=int(kcal),
        protein=int(protein),
        fat=int(fat),
        carb=int(carb),
    )

def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def get_today_stats(user_id: int) -> Tuple[Dict[str, float], List[FoodEntry]]:
    day = today_key()
    entries = FOOD_LOG.get(user_id, {}).get(day, [])
    total = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0}
    for e in entries:
        total["kcal"] += e.kcal
        total["protein"] += e.protein
        total["fat"] += e.fat
        total["carb"] += e.carb
    return total, entries

def add_food_entry(user_id: int, name: str, grams: int, product_info: Dict[str, float]) -> FoodEntry:
    factor = grams / 100.0
    entry = FoodEntry(
        name=name,
        grams=grams,
        kcal=int(product_info["kcal"] * factor),
        protein=round(product_info.get("protein", 0) * factor, 1),
        fat=round(product_info.get("fat", 0) * factor, 1),
        carb=round(product_info.get("carb", 0) * factor, 1),
    )
    day = today_key()
    FOOD_LOG.setdefault(user_id, {}).setdefault(day, []).append(entry)
    return entry

def generate_calorie_ring(consumed: float, target: float, filename: str = "ring.png") -> str:
    """
    Генерирует PNG с кольцом калорий (как в iOS Activity).
    """
    size = 600
    img = Image.new("RGB", (size, size), (20, 20, 30))
    draw = ImageDraw.Draw(img)

    center = size // 2
    radius = 220
    thickness = 40

    bbox = [
        center - radius,
        center - radius,
        center + radius,
        center + radius,
    ]

    start_angle = -90
    # фон-кольцо
    draw.arc(bbox, start=start_angle, end=start_angle + 359, fill=(60, 60, 80), width=thickness)

    if target <= 0:
        progress = 0
    else:
        progress = min(consumed / target, 1.5)  # до 150% цели

    end_angle = start_angle + int(360 * progress)
    color = (80, 200, 120) if progress <= 1 else (220, 80, 80)

    # цветное кольцо прогресса
    draw.arc(bbox, start=start_angle, end=end_angle, fill=color, width=thickness)

    text = f"{int(consumed)}/{int(target)} ккал"
    sub = f"{int((consumed / target) * 100) if target > 0 else 0}% от цели"

    try:
        font = ImageFont.truetype("arial.ttf", 40)
        font_sub = ImageFont.truetype("arial.ttf", 28)
    except IOError:
        font = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    tw, th = draw.textsize(text, font=font)
    draw.text((center - tw // 2, center - th // 2 - 20), text, font=font, fill=(240, 240, 240))

    sw, sh = draw.textsize(sub, font=font_sub)
    draw.text((center - sw // 2, center + th // 2), sub, font=font_sub, fill=(180, 180, 200))

    img.save(filename)
    return filename

def parse_meal_text_simple(text: str) -> Optional[Tuple[str, int]]:
    """
    Простой парсер: "продукт, граммы" -> (name, grams)
    """
    if "," in text:
        name_part, grams_part = [p.strip() for p in text.split(",", 1)]
        try:
            grams = int(grams_part)
        except ValueError:
            return None
        return name_part.lower(), grams
    return None

def generate_ai_advice(user: UserProfile, totals: Dict[str, float]) -> str:
    """
    Совет от ИИ (если есть ключ) или простой rule-based совет.
    """
    if not (OPENAI_API_KEY and openai):
        # простая логика без ИИ
        kcal = totals["kcal"]
        diff = user.daily.kcal - kcal
        if diff > 150:
            return "Сегодня ты в лёгком дефиците по калориям — это хорошо для похудения. Постарайся добрать белок и не уходить в слишком сильный минус."
        elif diff < -150:
            return "Сегодня есть превышение по калориям. На ужин сделай более лёгкий приём пищи и сократи быстрые углеводы."
        else:
            return "Сегодня ты почти в своей норме калорий. Продолжай в том же духе и следи за качеством продуктов."

    prompt = f"""
Ты — диетолог. У пользователя цель: {user.goal}.
Его параметры: возраст {user.age}, вес {user.weight} кг, рост {user.height} см.
Его дневная норма: {user.daily.kcal} ккал, белки {user.daily.protein} г, жиры {user.daily.fat} г, углеводы {user.daily.carb} г.
Факт за сегодня: калории {totals['kcal']}, белки {totals['protein']}, жиры {totals['fat']}, углеводы {totals['carb']}.

Дай короткий (до 4 предложений) понятный совет: что сегодня ок, что можно улучшить, и 1–2 конкретных рекомендации на следующий день.
Пиши по-русски, без обращения по имени и без смайлов.
"""
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты профессиональный диетолог."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=250,
        )
        return resp.choices[0].message["content"].strip()
    except Exception:
        return "Не удалось получить совет от ИИ, попробуй позже. А пока держи ориентир: придерживайся своей дневной нормы калорий и следи, чтобы белок не проседал."

# ==== РЕГИСТРАЦИЯ ====
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    if uid in USERS:
        await message.answer(
            "С возвращением! Я продолжаю считать твои калории.\n\n"
            "Используй кнопки внизу, чтобы добавить приём пищи или посмотреть прогресс.",
            reply_markup=main_keyboard(),
        )
        return
    await message.answer(
        "👋 Привет! Я твой личный бот-диетолог.\n"
        "Давай настроим профиль.\n\n"
        "Сколько тебе полных лет?",
    )
    await Register.age.set()

@dp.message_handler(state=Register.age)
async def reg_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text)
        if age < 10 or age > 90:
            raise ValueError
    except ValueError:
        await message.answer("Напиши возраст числом, например: 28")
        return
    await state.update_data(age=age)
    await message.answer("Напиши твой вес в кг (например: 82.5)")
    await Register.weight.set()

@dp.message_handler(state=Register.weight)
async def reg_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", "."))
        if weight < 30 or weight > 300:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес числом, например: 76.3")
        return
    await state.update_data(weight=weight)
    await message.answer("Теперь рост в см (например: 178)")
    await Register.height.set()

@dp.message_handler(state=Register.height)
async def reg_height(message: types.Message, state: FSMContext):
    try:
        height = int(message.text)
        if height < 120 or height > 230:
            raise ValueError
    except ValueError:
        await message.answer("Напиши рост числом, например: 180")
        return
    await state.update_data(height=height)
    await message.answer(
        "Выбери пол:\n"
        "М — мужчина\n"
        "Ж — женщина"
    )
    await Register.sex.set()

@dp.message_handler(state=Register.sex)
async def reg_sex(message: types.Message, state: FSMContext):
    t = message.text.strip().lower()
    if t.startswith("м"):
        sex = "m"
    elif t.startswith("ж"):
        sex = "f"
    else:
        await message.answer("Напиши просто М или Ж.")
        return
    await state.update_data(sex=sex)
    await message.answer(
        "Выбери уровень активности (ответь цифрой):\n"
        "1 — сидячая работа, нет тренировок\n"
        "2 — 1–3 лёгкие тренировки в неделю\n"
        "3 — 3–5 тренировок\n"
        "4 — физический труд или спорт почти каждый день"
    )
    await Register.activity.set()

@dp.message_handler(state=Register.activity)
async def reg_activity(message: types.Message, state: FSMContext):
    if message.text not in ACTIVITY_LEVELS:
        await message.answer("Выбери цифру от 1 до 4.")
        return
    await state.update_data(activity=message.text)
    await message.answer(
        "Какая цель? Ответь цифрой:\n"
        "1 — Похудеть\n"
        "2 — Удерживать вес\n"
        "3 — Набрать массу"
    )
    await Register.goal.set()

@dp.message_handler(state=Register.goal)
async def reg_goal(message: types.Message, state: FSMContext):
    if message.text not in ["1", "2", "3"]:
        await message.answer("Выбери 1, 2 или 3.")
        return
    goal_map = {"1": "loss", "2": "keep", "3": "gain"}
    await state.update_data(goal=goal_map[message.text])
    data = await state.get_data()
    age = data["age"]
    weight = data["weight"]
    height = data["height"]
    sex = data["sex"]
    activity = data["activity"]
    goal = data["goal"]

    daily = calc_daily_norm(weight, height, age, sex, activity, goal)
    uid = message.from_user.id
    USERS[uid] = UserProfile(
        age=age,
        weight=weight,
        height=height,
        sex=sex,
        activity=activity,
        goal=goal,
        daily=daily,
    )
    await state.finish()

    goal_text = {"loss": "Похудение", "keep": "Удержание веса", "gain": "Набор массы"}[goal]
    await message.answer(
        "Готово! Я посчитал твою дневную норму:\n\n"
        f"🎯 Цель: <b>{goal_text}</b>\n"
        f"🔥 Калории: <b>{daily.kcal}</b> ккал\n"
        f"🍗 Белки: <b>{daily.protein}</b> г\n"
        f"🧈 Жиры: <b>{daily.fat}</b> г\n"
        f"🍚 Углеводы: <b>{daily.carb}</b> г\n\n"
        "Теперь просто отправляй, что ты ешь — текстом, голосом или фото.\n"
        "Или нажми «➕ Приём пищи».",
        reply_markup=main_keyboard(),
    )

# ==== ДОБАВЛЕНИЕ ПРИЁМА ПИЩИ ====
@dp.message_handler(lambda m: m.text == "➕ Приём пищи")
async def start_add_meal(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in USERS:
        await message.answer("Сначала нужно пройти регистрацию: /start")
        return
    await message.answer(
        "Отправь, что ты сейчас съел(а):\n\n"
        "• текстом: <code>плов, 250</code>\n"
        "• голосовым (дальше можно прикрутить распознавание)\n"
        "• фото тарелки (можно прикрутить распознавание по картинке)\n\n"
        "Если пишешь текстом — лучше формат <b>продукт, граммы</b>.",
    )
    await AddMeal.waiting_input.set()

@dp.message_handler(state=AddMeal.waiting_input, content_types=[types.ContentType.TEXT])
async def add_meal_text(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in USERS:
        await state.finish()
        await message.answer("Сначала нужно пройти регистрацию: /start")
        return

    parsed = parse_meal_text_simple(message.text.lower())
    if not parsed:
        await message.answer(
            "Не смог понять формат.\n"
            "Напиши, пожалуйста, в виде: <b>продукт, граммы</b>, например: <code>плов, 250</code>."
        )
        return

    name, grams = parsed
    if name not in PRODUCTS:
        await message.answer(
            f"Я ещё не знаю продукт <b>{name}</b>. Напиши его калорийность на 100 г (ккал), например: 210"
        )
        await state.update_data(temp_name=name, grams=grams)
        return

    entry = add_food_entry(uid, name, grams, PRODUCTS[name])
    totals, _ = get_today_stats(uid)
    await state.finish()
    await message.answer(
        f"Добавил: <b>{entry.name}</b>, {entry.grams} г — ~{entry.kcal} ккал.\n"
        f"Сегодня уже примерно <b>{int(totals['kcal'])}</b> ккал.",
        reply_markup=main_keyboard(),
    )

@dp.message_handler(state=AddMeal.waiting_input, content_types=[types.ContentType.VOICE])
async def add_meal_voice(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in USERS:
        await state.finish()
        await message.answer("Сначала нужно пройти регистрацию: /start")
        return

    # здесь можно подключить Whisper / GPT-4o для распознавания речи из voice
    await message.answer(
        "Я получил голосовое. В продакшене здесь можно включить ИИ для распознавания.\n"
        "Чтобы сейчас не ломать логику, отправь, пожалуйста, то же самое текстом в формате <b>продукт, граммы</b>."
    )

@dp.message_handler(state=AddMeal.waiting_input, content_types=[types.ContentType.PHOTO])
async def add_meal_photo(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if uid not in USERS:
        await state.finish()
        await message.answer("Сначала нужно пройти регистрацию: /start")
        return

    # здесь можно подключить модель CV / GPT-4o Vision для анализа фото
    await message.answer(
        "Я получил фото еды. Здесь можно подключить ИИ, который распознаёт блюда и порции.\n"
        "Пока что отправь, пожалуйста, приём пищи текстом в формате <b>продукт, граммы</b>."
    )

@dp.message_handler(state=AddMeal.waiting_input)
async def add_meal_other(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer(
        "Поддерживаются текст, голос и фото. Попробуй ещё раз через «➕ Приём пищи».",
        reply_markup=main_keyboard(),
    )

# ввод калорийности нового продукта (когда бот спросил)
@dp.message_handler(lambda m: m.text and m.text.isdigit(), state="*")
async def handle_custom_kcal(message: types.Message, state: FSMContext):
    data = await state.get_data()
    if "temp_name" not in data or "grams" not in data:
        return

    try:
        kcal100 = float(message.text.replace(",", "."))
        if kcal100 <= 0 or kcal100 > 900:
            raise ValueError
    except ValueError:
        await message.answer("Напиши калорийность на 100 г числом, например: 210")
        return

    name = data["temp_name"]
    grams = data["grams"]
    PRODUCTS[name] = {"kcal": kcal100, "protein": 0.0, "fat": 0.0, "carb": 0.0}
    uid = message.from_user.id
    entry = add_food_entry(uid, name, grams, PRODUCTS[name])
    totals, _ = get_today_stats(uid)
    await state.finish()
    await message.answer(
        f"Добавил новый продукт: <b>{entry.name}</b>, {entry.grams} г — ~{entry.kcal} ккал.\n"
        f"Сегодня уже примерно <b>{int(totals['kcal'])}</b> ккал.",
        reply_markup=main_keyboard(),
    )

# ==== МОЙ ДЕНЬ (кольцо калорий) ====
@dp.message_handler(lambda m: m.text == "📊 Мой день")
async def show_today(message: types.Message):
    uid = message.from_user.id
    user = USERS.get(uid)
    if not user:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    totals, entries = get_today_stats(uid)
    img_path = generate_calorie_ring(totals["kcal"], user.daily.kcal, filename=f"ring_{uid}.png")

    caption_lines = [
        "📊 <b>Твой день по питанию</b>\n",
        f"Калории: <b>{int(totals['kcal'])}</b> из <b>{user.daily.kcal}</b> ккал",
        f"Белки: <b>{round(totals['protein'],1)}</b> / {user.daily.protein} г",
        f"Жиры: <b>{round(totals['fat'],1)}</b> / {user.daily.fat} г",
        f"Углеводы: <b>{round(totals['carb'],1)}</b> / {user.daily.carb} г\n",
    ]

    if entries:
        caption_lines.append("<b>Сегодня ты ел(а):</b>")
        for e in entries[-10:]:
            caption_lines.append(f"• {e.name}, {e.grams} г — {e.kcal} ккал")
    else:
        caption_lines.append("Приёмов пищи пока нет. Нажми «➕ Приём пищи».")

    with open(img_path, "rb") as photo:
        await message.answer_photo(photo=photo, caption="\n".join(caption_lines), reply_markup=main_keyboard())

# ==== ПРОГРЕСС ====
@dp.message_handler(lambda m: m.text == "📈 Прогресс")
async def show_progress(message: types.Message):
    uid = message.from_user.id
    user = USERS.get(uid)
    if not user:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    user_days = FOOD_LOG.get(uid, {})
    if not user_days:
        await message.answer("Пока нет статистики. Начни добавлять приёмы пищи.")
        return

    lines = ["📈 <b>Последние дни:</b>\n"]
    for day in sorted(user_days.keys(), reverse=True)[:7]:
        entries = user_days[day]
        kcal = sum(e.kcal for e in entries)
        mark = "✅" if abs(kcal - user.daily.kcal) < user.daily.kcal * 0.15 else ("⬆️" if kcal > user.daily.kcal else "⬇️")
        lines.append(f"{day}: {mark} {kcal} ккал")

    await message.answer("\n".join(lines), reply_markup=main_keyboard())

# ==== ПРОФИЛЬ / ЦЕЛЬ ====
@dp.message_handler(lambda m: m.text == "⚙️ Профиль")
async def show_profile(message: types.Message):
    uid = message.from_user.id
    user = USERS.get(uid)
    if not user:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    goal_text = {"loss": "Похудение", "keep": "Удержание веса", "gain": "Набор массы"}[user.goal]
    await message.answer(
        "⚙️ <b>Твой профиль</b>\n\n"
        f"Возраст: {user.age}\n"
        f"Вес: {user.weight} кг\n"
        f"Рост: {user.height} см\n"
        f"Цель: {goal_text}\n"
        f"Дневная норма: {user.daily.kcal} ккал\n\n"
        "Если хочешь поменять параметры — просто снова введи /start и пройди настройки заново.",
        reply_markup=main_keyboard(),
    )

# ==== СОВЕТ ОТ ИИ ====
@dp.message_handler(lambda m: m.text == "💬 Совет от ИИ")
async def ai_advice(message: types.Message):
    uid = message.from_user.id
    user = USERS.get(uid)
    if not user:
        await message.answer("Сначала пройди регистрацию: /start")
        return

    totals, _ = get_today_stats(uid)
    advice = generate_ai_advice(user, totals)
    await message.answer(f"💬 <b>Разбор дня</b>\n\n{advice}", reply_markup=main_keyboard())

# ==== ЗАПУСК ====
if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Set BOT_TOKEN env variable or put your token into BOT_TOKEN constant.")
    else:
        executor.start_polling(dp, skip_updates=True)
