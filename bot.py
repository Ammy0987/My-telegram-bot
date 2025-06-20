import os
import logging
import asyncio
import re
import time
from functools import wraps

import aiosqlite
from langdetect import detect, LangDetectException
from telegram import Update, ChatAction
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

import openai

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Load env variables
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")  # Password for admin commands

if not TELEGRAM_TOKEN or not OPENAI_API_KEY or ADMIN_ID == 0:
    raise ValueError("Missing required environment variables: BOT_TOKEN, OPENAI_API_KEY, ADMIN_ID")

openai.api_key = OPENAI_API_KEY

# Constants
RATE_LIMIT_SECONDS = 5
CHAT_HISTORY_LIMIT = 20
CACHE_EXPIRY_SECONDS = 3600  # 1 hour cache expiry

# In-memory caches
user_cache = {}  # {user_id: {"last_message_time": timestamp, "history": [...]}}
response_cache = {}  # {(user_id, message_hash): (response_text, timestamp)}

# Admin authenticated users
authenticated_admins = set()

# Database path
DB_PATH = "users_async.db"


# Utility Functions
def sanitize_text(text: str) -> str:
    text = text.strip()
    if len(text) > 1000:
        text = text[:1000] + "..."
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    return text


async def get_db_connection():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    async with await get_db_connection() as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                count INTEGER,
                language TEXT,
                location TEXT,
                last_message_time REAL DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp REAL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID or user_id not in authenticated_admins:
            await update.message.reply_text("⛔ Huna ruhusa ya kutumia command hii. Tafadhali thibitisha kwanza kwa /auth <password>")
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


# Admin auth command
async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("Tafadhali tuma /auth <password>")
        return
    password = context.args[0]
    if user_id == ADMIN_ID and password == ADMIN_PASSWORD:
        authenticated_admins.add(user_id)
        await update.message.reply_text("✅ Umefanikiwa kuthibitishwa kama admin.")
        logging.info(f"Admin {user_id} authenticated successfully.")
    else:
        await update.message.reply_text("❌ Nywila si sahihi.")
        logging.warning(f"Admin authentication failed for user {user_id}.")


# Rate limiting decorator
def rate_limited(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        now = time.time()
        last_time = user_cache.get(user_id, {}).get("last_message_time", 0)
        if now - last_time < RATE_LIMIT_SECONDS:
            await update.message.reply_text(f"⌛ Tafadhali ngoja sekunde {int(RATE_LIMIT_SECONDS - (now - last_time))} kabla ya kutuma ujumbe mwingine.")
            return
        user_cache.setdefault(user_id, {})["last_message_time"] = now
        return await func(update, context, *args, **kwargs)

    return wrapper


# Update or insert user info in DB
async def update_user(user_id: int, name: str, language: str):
    async with await get_db_connection() as db:
        cursor = await db.execute("SELECT count FROM users WHERE user_id=?", (user_id,))
        row = await cursor.fetchone()
        if row:
            count = row[0] + 1
            await db.execute(
                "UPDATE users SET count=?, language=? WHERE user_id=?",
                (count, language, user_id),
            )
        else:
            count = 1
            await db.execute(
                "INSERT INTO users (user_id, name, count, language, location, last_message_time) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, count, language, "Unknown", 0),
            )
        await db.commit()


async def save_chat_message(user_id: int, role: str, content: str):
    async with await get_db_connection() as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        # Keep only last CHAT_HISTORY_LIMIT messages per user to save space
        await db.execute(
            f"""
            DELETE FROM chat_history WHERE id NOT IN (
                SELECT id FROM chat_history WHERE user_id = ? ORDER BY timestamp DESC LIMIT {CHAT_HISTORY_LIMIT}
            ) AND user_id = ?
            """,
            (user_id, user_id),
        )
        await db.commit()


async def get_chat_history(user_id: int):
    async with await get_db_connection() as db:
        cursor = await db.execute(
            "SELECT role, content FROM chat_history WHERE user_id=? ORDER BY timestamp ASC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]


def is_blocked(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    blocked = context.bot_data.get("blocked", set())
    return user_id in blocked


async def send_typing_action(func):
    @wraps(func)
    async def command_func(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        return await func(update, context, *args, **kwargs)
    return command_func


@send_typing_action
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Karibu! Mimi ni bot wa AI mwenye akili ya juu na uwezo wa ajabu.")


@send_typing_action
@rate_limited
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = sanitize_text(update.message.text)

    if is_blocked(user_id, context):
        logging.info(f"Blocked user {user_id} attempted to send a message.")
        return  # silently ignore blocked users

    if not text:
        await update.message.reply_text("❌ Ujumbe wako haujakubalika, tafadhali jaribu tena.")
        return

    # Detect language robustly
    try:
        lang = detect(text)
    except LangDetectException:
        lang = user.language_code or "sw"

    await update_user(user_id, user.first_name or "", lang)

    # Get chat history
    history = await get_chat_history(user_id)
    history.append({"role": "user", "content": text})
    history = history[-CHAT_HISTORY_LIMIT:]

    # Cache check
    message_key = (user_id, hash(text))
    cached_response = response_cache.get(message_key)
    if cached_response:
        cached_text, timestamp = cached_response
        if time.time() - timestamp < CACHE_EXPIRY_SECONDS:
            await update.message.reply_text(cached_text + "\n\n_(majibu yaliyohifadhiwa)_")
            return

    system_prompt = {
        "role": "system",
        "content": (
            "Wewe ni roboti mwerevu sana na mkarimu wa Telegram AI, "
            "anajibu kwa lugha yoyote, hutoa maelezo ya kina, na anafuata sheria za ethical hacking. "
            "Daima tumia mitaala ya mkoa wa mtumiaji kwa elimu."
        ),
    }

    messages = [system_prompt] + history

    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.7,
            max_tokens=800,
        )
        bot_reply = response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI API error: {e}")
        await update.message.reply_text("⚠️ Samahani, kuna tatizo la ndani. Jaribu tena baadaye.")
        return

    await save_chat_message(user_id, "user", text)
    await save_chat_message(user_id, "assistant", bot_reply)

    # Cache response
    response_cache[message_key] = (bot_reply, time.time())

    await update.message.reply_text(bot_reply)


@admin_only
async def all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with await get_db_connection() as db:
        cursor = await db.execute("SELECT * FROM users")
        rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("📭 Hakuna watumiaji bado.")
        return

    msg = "📊 Watumiaji waliopo:\n"
    for r in rows:
        msg += f"{r[1]} (ID: {r[0]}) - Maswali: {r[2]} - Lugha: {r[3]}\n"
    await update.message.reply_text(msg)


@admin_only
async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Tumia: /block <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID lazima iwe nambari.")
        return

    blocked = context.bot_data.setdefault("blocked", set())
    blocked.add(user_id)
    await update.message.reply_text(f"🚫 Mtumiaji {user_id} amezuiwa.")


@admin_only
async def unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("⚠️ Tumia: /unblock <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID lazima iwe nambari.")
        return

    blocked = context.bot_data.get("blocked", set())
    if user_id in blocked:
        blocked.remove(user_id)
        await update.message.reply_text(f"✅ Mtumiaji {user_id} ameruhusiwa tena.")
    else:
        await update.message.reply_text(f"ℹ️ Mtumiaji {user_id} hana block.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Command za bot:\n"
        "/start - Anza mazungumzo\n"
        "/help - Orodha ya commands\n"
        "/auth <password> - Thibitisha kama admin\n"
        "/all_users - Onyesha watumiaji (admin tu)\n"
        "/block <user_id> - Zuia mtumiaji (admin tu)\n"
        "/unblock <user
