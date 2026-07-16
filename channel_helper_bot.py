# -*- coding: utf-8 -*-
"""
Бот-помощник для Telegram-канала по химии.

ЧТО ДЕЛАЕТ:
- 07:00 — приветствие с названием дня недели
- 09:00 — сложный опрос (квиз) в группе обсуждений
- 13:40 — сколько дней осталось до экзамена в вуз (15 июля)
- 17:00 — второй, другой сложный опрос
- В праздники — поздравление в канале
- Модерация: мат/оскорбления в группе обсуждений → удаление сообщения,
  1-е нарушение — предупреждение, 2-е — исключение из группы и канала
- Раз в неделю (воскресенье, 20:00) — похвала ученика, лучше всех
  отвечавшего на опросы за неделю, по имени, в канале

КАК ЗАПУСТИТЬ:
1. pip install "python-telegram-bot[job-queue]"
2. Создайте нового бота через @BotFather, получите токен.
3. Добавьте бота АДМИНИСТРАТОРОМ и в канал, и в группу обсуждений
   (комментарии), с правами: отправка сообщений, удаление сообщений,
   блокировка пользователей.
4. Впишите токен, ID канала и ID группы обсуждений ниже (или через
   переменные окружения BOT_TOKEN / CHANNEL_ID / GROUP_ID на Render).
5. Запустите: python channel_helper_bot.py

КАК УЗНАТЬ ID ГРУППЫ ОБСУЖДЕНИЙ:
Перешлите любое сообщение из группы обсуждений боту @userinfobot,
либо временно добавьте бота @RawDataBot в группу — он покажет chat_id
(будет отрицательным числом вида -1001234567890).
"""

import json
import os
import random
import re
import threading
import urllib.request
from datetime import datetime, date
from datetime import time as dtime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    ContextTypes,
    filters,
)

TZ = ZoneInfo("Asia/Tashkent")

# ========================= НАСТРОЙКИ =========================
TOKEN = os.environ.get("BOT_TOKEN", "8724862130:AAFoj0RznHbCtYjq1Nl-R7XUkvgdbgSyEpE").strip()

# Канал: username вида "@my_channel" либо числовой ID (-100...)
_channel_raw = os.environ.get("CHANNEL_ID", "-1003846503056").strip()
try:
    CHANNEL_ID = int(_channel_raw)
except ValueError:
    CHANNEL_ID = _channel_raw  # на случай, если задали как @username

# Группа обсуждений (комментарии под постами канала): числовой ID (-100...)
_group_raw = os.environ.get("GROUP_ID", "-1003509282945").strip()
try:
    GROUP_ID = int(_group_raw)
except ValueError:
    GROUP_ID = _group_raw  # на случай, если задали как @username

try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "1364771293"))
except ValueError:
    ADMIN_ID = 1364771293

# Дата экзамена в вуз (день и месяц — год бот определяет сам, всегда
# показывая ближайшую предстоящую дату)
EXAM_MONTH = int(os.environ.get("EXAM_MONTH", "7"))
EXAM_DAY = int(os.environ.get("EXAM_DAY", "15"))


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


# ========================= ХРАНИЛИЩЕ =========================
STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_bot_store.json")

JSONBIN_API_KEY = os.environ.get("JSONBIN_API_KEY", "").strip()
JSONBIN_BIN_ID = os.environ.get("JSONBIN_BIN_ID", "").strip()
_USE_CLOUD_STORE = bool(JSONBIN_API_KEY and JSONBIN_BIN_ID)


def load_store():
    if _USE_CLOUD_STORE:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
            req = urllib.request.Request(
                url,
                headers={
                    "X-Master-Key": JSONBIN_API_KEY,
                    "User-Agent": "Mozilla/5.0 (compatible; ChannelBot/1.0)",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("record", {})
        except Exception as e:
            print(f"⚠ Не удалось загрузить облачное хранилище: {e}")
            return {}
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_store():
    if _USE_CLOUD_STORE:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
            payload = json.dumps(STORE).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="PUT",
                headers={
                    "X-Master-Key": JSONBIN_API_KEY,
                    "Content-Type": "application/json",
                    "X-Bin-Versioning": "false",
                    "User-Agent": "Mozilla/5.0 (compatible; ChannelBot/1.0)",
                },
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
        except Exception as e:
            print(f"⚠ Не удалось сохранить в облачное хранилище: {e}")
        return
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(STORE, f, ensure_ascii=False, indent=2)


STORE = load_store()

# ========================= БАНК СЛОЖНЫХ ВОПРОСОВ =========================
# Формат: (вопрос, [варианты], индекс правильного, короткое пояснение)
QUIZ_BANK = [
    ("Какая гибридизация у атома углерода в молекуле этина (ацетилена) C2H2?",
     ["sp", "sp2", "sp3", "sp3d"], 0, "В тройной связи C≡C углерод находится в sp-гибридизации."),
    ("Сколько сигма- и пи-связей в молекуле бензола C6H6?",
     ["6σ и 3π", "12σ и 3π", "6σ и 6π", "9σ и 3π"], 1, "6 C-H + 6 C-C сигма-связей = 12σ, плюс 3 делокализованные π-связи."),
    ("Какой объём (л, н.у.) займут 3 моль идеального газа?",
     ["22.4", "44.8", "67.2", "11.2"], 2, "V = n×Vm = 3×22.4 = 67.2 л."),
    ("Какое вещество образуется при взаимодействии этилена с бромной водой?",
     ["1,1-дибромэтан", "1,2-дибромэтан", "бромэтан", "этиленгликоль"], 1,
     "Присоединение брома по двойной связи даёт 1,2-дибромэтан (реакция обесцвечивания бромной воды)."),
    ("Чему равна степень окисления серы в тиосульфате натрия Na2S2O3?",
     ["+2", "+4", "+6", "0"], 0, "Средняя степень окисления серы в S2O3²⁻ равна +2."),
    ("Какой из перечисленных оксидов является амфотерным?",
     ["CO2", "Al2O3", "Na2O", "SO3"], 1, "Al2O3 проявляет и кислотные, и основные свойства."),
    ("Сколько граммов чистого вещества содержится в 200 г 15%-ного раствора?",
     ["15 г", "20 г", "30 г", "45 г"], 2, "m = 200×0.15 = 30 г."),
    ("Какой тип гибридизации у атома азота в молекуле аммиака NH3?",
     ["sp", "sp2", "sp3", "sp3d"], 2, "Азот в NH3 — sp3 (три связи + неподелённая пара)."),
    ("Какая соль образуется при неполной нейтрализации H3PO4 гидроксидом натрия (1:1)?",
     ["Na3PO4", "Na2HPO4", "NaH2PO4", "Na4P2O7"], 2, "При соотношении 1:1 образуется дигидрофосфат натрия."),
    ("Какой газ выделяется при разложении нитрата аммония при нагревании (без взрыва)?",
     ["N2", "NO2", "N2O", "NH3"], 2, "NH4NO3 → N2O + 2H2O (оксид азота(I), «веселящий газ»)."),
    ("Сколько изомерных алкенов состава C4H8 существует (без учёта цис-транс изомерии колец)?",
     ["2", "3", "4", "5"], 2, "Бутен-1, бутен-2 (цис/транс считаем отдельно), изобутилен — всего 4 структурных/пространственных изомера."),
    ("Какой продукт образуется при окислении первичного спирта перманганатом калия в кислой среде (избыток окислителя)?",
     ["Альдегид", "Карбоновая кислота", "Кетон", "Простой эфир"], 1,
     "Избыток сильного окислителя окисляет первичный спирт до карбоновой кислоты."),
    ("Какова молярная масса эквивалента серной кислоты H2SO4 в реакции полной нейтрализации?",
     ["98 г/моль", "49 г/моль", "32.7 г/моль", "196 г/моль"], 1, "M(экв) = M/2 = 98/2 = 49 г/моль."),
    ("Какой комплексный ион образуется при растворении гидроксида цинка в избытке щёлочи?",
     ["[Zn(OH)4]2-", "[Zn(OH)2]", "ZnO2 2-", "[Zn(OH)6]4-"], 0, "Zn(OH)2 + 2NaOH → Na2[Zn(OH)4]."),
    ("Какое вещество является продуктом реакции Кучерова (гидратация ацетилена)?",
     ["Этанол", "Уксусный альдегид", "Этиленгликоль", "Уксусная кислота"], 1,
     "Гидратация ацетилена по Кучерову даёт ацетальдегид (правило Марковникова, катализатор Hg2+)."),
    ("Сколько граммов осадка BaSO4 выпадет при смешении растворов, содержащих 0.2 моль BaCl2 и избыток Na2SO4?",
     ["23.3 г", "46.6 г", "11.65 г", "58.25 г"], 1, "n(BaSO4)=0.2 моль, M=233 г/моль, m=0.2×233=46.6 г."),
    ("Какой из галогенов проявляет наибольшую окислительную способность?",
     ["Йод", "Бром", "Хлор", "Фтор"], 3, "Окислительная способность галогенов растёт снизу вверх по группе: F2 — самый сильный окислитель."),
    ("Какая формула у среднего (нормального) карбоната кальция?",
     ["Ca(HCO3)2", "CaCO3", "CaO", "Ca(OH)2"], 1, "Средняя соль угольной кислоты и кальция — CaCO3."),
    ("Что происходит с равновесием реакции N2+3H2⇌2NH3 (экзотермична) при повышении температуры?",
     ["Смещается вправо", "Смещается влево", "Не меняется", "Реакция останавливается"], 1,
     "Для экзотермической реакции повышение температуры смещает равновесие в сторону эндотермического процесса — влево."),
    ("Сколько π-связей в молекуле бутадиена-1,3 (CH2=CH-CH=CH2)?",
     ["1", "2", "3", "4"], 1, "Две изолированные (сопряжённые) двойные связи — значит, 2 π-связи."),
    ("Какой объём 2М раствора HCl потребуется для полной нейтрализации 0.4 моль NaOH?",
     ["100 мл", "200 мл", "400 мл", "800 мл"], 1, "n(HCl)=n(NaOH)=0.4 моль. V=n/C=0.4/2=0.2 л=200 мл."),
    ("Какой продукт образуется при полном гидролизе жиров (триглицеридов)?",
     ["Глицерин и высшие карбоновые кислоты", "Глюкоза и этанол", "Аминокислоты", "Крахмал"], 0,
     "Жиры — сложные эфиры глицерина и высших карбоновых кислот, при гидролизе распадаются на них."),
]


def get_used_quiz_indices():
    return STORE.get("used_quiz_indices", [])


def pick_quiz_question():
    """Выбирает вопрос так, чтобы не повторяться, пока не закончится весь банк."""
    used = STORE.get("used_quiz_indices", [])
    available = [i for i in range(len(QUIZ_BANK)) if i not in used]
    if not available:
        used = []
        available = list(range(len(QUIZ_BANK)))
    idx = random.choice(available)
    used.append(idx)
    if len(used) >= len(QUIZ_BANK):
        used = used[-(len(QUIZ_BANK) - 1):] if len(QUIZ_BANK) > 1 else []
    STORE["used_quiz_indices"] = used
    save_store()
    return QUIZ_BANK[idx]


# ========================= ПРИВЕТСТВИЯ ПО ДНЯМ НЕДЕЛИ =========================
WEEKDAY_GREETINGS = {
    0: "Сегодня понедельник — начало новой недели! 💪 Начнём готовиться с новыми силами.",
    1: "Сегодня вторник. Продолжаем двигаться к цели шаг за шагом! 🔬",
    2: "Сегодня среда — середина недели. Половина пути уже пройдена, не сбавляй темп! ⚗️",
    3: "Сегодня четверг. Ещё немного — и выходные, но сначала — знания! 📚",
    4: "Сегодня пятница! Заверши неделю на высокой ноте — отдых уже скоро, ты это заслужил. 🎉",
    5: "Сегодня суббота. Хороший день, чтобы спокойно повторить пройденное. ☀️",
    6: "Сегодня воскресенье — последний день перед новой неделей. Подготовься и отдохни как следует! 🌙",
}

# ========================= ПРАЗДНИКИ =========================
# Даты с фиксированным числом. Подвижные даты (Рамазон-хайит, Курбан-хайит)
# каждый год разные — добавляйте их вручную сюда на текущий год.
HOLIDAYS = {
    (1, 1): "🎉 С Новым годом! Пусть этот год принесёт много успехов в учёбе и жизни!",
    (1, 14): "🎖 С Днём защитников Родины!",
    (3, 8): "🌷 С Международным женским днём! Дорогие девушки, желаю вам вдохновения и успехов!",
    (3, 21): "🌸 С праздником Навруз! Пусть этот день принесёт тепло, добро и новые силы!",
    (5, 9): "🕊 С Днём памяти и почестей!",
    (9, 1): "🇺🇿 С Днём Независимости Узбекистана! Поздравляю всех с этим важным праздником!",
    (10, 1): "🍎 С Днём учителей и наставников! Спасибо всем педагогам за их труд!",
    (12, 8): "📜 С Днём Конституции Республики Узбекистан!",
    # (месяц, день): "текст поздравления с Рамазон-хайит" — обновляйте ежегодно
    # (месяц, день): "текст поздравления с Курбан-хайит" — обновляйте ежегодно
}


# ========================= МОДЕРАЦИЯ =========================
BAD_WORDS = [
    "сука", "суки", "сучка", "блять", "бля", "бляд", "хуй", "хуе", "хуё",
    "пизд", "пизда", "ебан", "ёбан", "еба", "ебат", "мудак", "мудило",
    "гандон", "долбоеб", "долбоёб", "уебок", "уёбок", "чмо", "тварь",
    "ублюдок", "сволочь", "гнида", "падла", "идиот", "дебил", "придурок",
    "дура", "дурак", "тупица", "кретин", "ишак", "скотина", "урод",
]


def normalize_text(text):
    text = text.lower()
    replacements = {"0": "о", "3": "е", "1": "l", "@": "а", "$": "с", "*": ""}
    for a, b in replacements.items():
        text = text.replace(a, b)
    return re.sub(r"[^а-яёa-z]", "", text)


def contains_bad_word(text):
    norm = normalize_text(text)
    return any(word in norm for word in BAD_WORDS)


def get_warnings():
    return STORE.get("warnings", {})


def add_warning(user_id):
    warnings = get_warnings()
    count = warnings.get(str(user_id), 0) + 1
    warnings[str(user_id)] = count
    STORE["warnings"] = warnings
    save_store()
    return count


# ========================= РЕЙТИНГ ОТВЕТОВ НА ОПРОСЫ =========================
def get_scores():
    return STORE.get("scores", {})


def add_score(user_id, name, correct):
    scores = get_scores()
    entry = scores.get(str(user_id), {"correct": 0, "total": 0, "name": name})
    entry["total"] += 1
    entry["name"] = name
    if correct:
        entry["correct"] += 1
    scores[str(user_id)] = entry
    STORE["scores"] = scores
    save_store()


def register_poll(poll_id, correct_option_id):
    polls = STORE.get("active_polls", {})
    polls[poll_id] = correct_option_id
    # не храним больше 50 последних опросов, чтобы файл не рос бесконечно
    if len(polls) > 50:
        for old_key in list(polls.keys())[:-50]:
            polls.pop(old_key, None)
    STORE["active_polls"] = polls
    save_store()


def get_poll_correct(poll_id):
    return STORE.get("active_polls", {}).get(poll_id)


# ========================= ЗАДАЧИ ПО РАСПИСАНИЮ =========================
async def send_quiz(context: ContextTypes.DEFAULT_TYPE):
    q = pick_quiz_question()
    question, options, correct_idx, explanation = q
    msg = await context.bot.send_poll(
        chat_id=GROUP_ID,
        question=f"🧪 {question}",
        options=options,
        type="quiz",
        correct_option_id=correct_idx,
        is_anonymous=False,
        explanation=explanation,
    )
    register_poll(msg.poll.id, correct_idx)


async def job_quiz_morning(context: ContextTypes.DEFAULT_TYPE):
    await send_quiz(context)


async def job_quiz_evening(context: ContextTypes.DEFAULT_TYPE):
    await send_quiz(context)


async def job_morning_greeting(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    text = WEEKDAY_GREETINGS.get(today.weekday(), "Доброе утро! Хорошего дня!")
    await context.bot.send_message(CHANNEL_ID, f"☀️ {text}")


async def job_exam_countdown(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    exam_date = date(today.year, EXAM_MONTH, EXAM_DAY)
    if exam_date < today:
        exam_date = date(today.year + 1, EXAM_MONTH, EXAM_DAY)
    days_left = (exam_date - today).days
    await context.bot.send_message(
        CHANNEL_ID,
        f"⏳ До экзамена в вуз ({exam_date.strftime('%d.%m.%Y')}) осталось {days_left} дней. "
        "Каждый день на счету — готовься усердно! 📖",
    )


async def job_holiday_check(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()
    text = HOLIDAYS.get((today.month, today.day))
    if text:
        await context.bot.send_message(CHANNEL_ID, text)


async def job_weekly_praise(context: ContextTypes.DEFAULT_TYPE):
    scores = get_scores()
    if not scores:
        return
    best_id, best = max(
        scores.items(),
        key=lambda kv: (kv[1]["correct"], kv[1]["correct"] / max(kv[1]["total"], 1)),
    )
    if best["correct"] == 0:
        return
    name = best.get("name") or "Ученик"
    await context.bot.send_message(
        CHANNEL_ID,
        f"🏆 На этой неделе лучше всех отвечал(а) на опросы — {name}! "
        f"Правильных ответов: {best['correct']} из {best['total']}. Так держать! 👏🎉",
    )
    STORE["scores"] = {}
    save_store()


# ========================= ХЭНДЛЕРЫ =========================
async def poll_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.poll_answer
    if not answer.option_ids:
        return
    user = answer.user
    correct = get_poll_correct(answer.poll_id)
    if correct is None:
        return
    is_correct = correct in answer.option_ids
    add_score(user.id, user.full_name, is_correct)


async def moderation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user:
        return
    if is_admin(user.id):
        return
    if not contains_bad_word(msg.text):
        return

    try:
        await msg.delete()
    except Exception:
        pass

    count = add_warning(user.id)
    if count == 1:
        await context.bot.send_message(
            GROUP_ID,
            f"⚠️ {user.full_name}, пожалуйста, не используйте оскорбления и нецензурную лексику. "
            "Это предупреждение. При повторе вы будете исключены из группы и канала.",
        )
    else:
        try:
            await context.bot.ban_chat_member(GROUP_ID, user.id)
        except Exception:
            pass
        try:
            await context.bot.ban_chat_member(CHANNEL_ID, user.id)
        except Exception:
            pass
        await context.bot.send_message(
            GROUP_ID,
            f"⛔️ {user.full_name} был(а) исключён(а) за повторное нарушение правил общения.",
        )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот-помощник канала.\n\n"
        "Я сам публикую опросы, поздравления и слежу за порядком в комментариях — "
        "мной не нужно управлять вручную."
    )


async def rating_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий рейтинг недели (для проверки админом)."""
    if not is_admin(update.effective_user.id):
        return
    scores = get_scores()
    if not scores:
        await update.message.reply_text("Пока никто не отвечал на опросы на этой неделе.")
        return
    lines = sorted(scores.values(), key=lambda e: e["correct"], reverse=True)[:10]
    text = "📊 Текущий рейтинг недели:\n\n" + "\n".join(
        f"{i+1}. {e['name']} — {e['correct']}/{e['total']}" for i, e in enumerate(lines)
    )
    await update.message.reply_text(text)


# ========================= HEALTH-CHECK ДЛЯ RENDER =========================
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def _run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


# ========================= MAIN =========================
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("rating", rating_command))
    app.add_handler(PollAnswerHandler(poll_answer_handler))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.Chat(GROUP_ID) & ~filters.COMMAND, moderation_handler)
    )

    jq = app.job_queue
    jq.run_daily(job_morning_greeting, time=dtime(hour=7, minute=0, tzinfo=TZ))
    jq.run_daily(job_quiz_morning, time=dtime(hour=9, minute=0, tzinfo=TZ))
    jq.run_daily(job_exam_countdown, time=dtime(hour=13, minute=40, tzinfo=TZ))
    jq.run_daily(job_quiz_evening, time=dtime(hour=17, minute=0, tzinfo=TZ))
    jq.run_daily(job_holiday_check, time=dtime(hour=8, minute=0, tzinfo=TZ))
    # Похвала лучшего ученика — каждое воскресенье в 20:00 (6 = воскресенье)
    jq.run_daily(job_weekly_praise, time=dtime(hour=20, minute=0, tzinfo=TZ), days=(6,))

    threading.Thread(target=_run_health_server, daemon=True).start()
    app.run_polling()


if __name__ == "__main__":
    main()
