bot.py

import logging import os import requests from telegram import Update, ForceReply from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

Basic logging

logging.basicConfig( format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO ) logger = logging.getLogger(name)

HuggingFace Inference

HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN") API_URL = "https://api-inference.huggingface.co/models/google/flan-t5-large" HEADERS = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"}

async def ask_ai(question): # Sanitize rude or slang input without responding rudely filtered_question = question.lower() if any(word in filtered_question for word in ["fuck", "shit", "ngoma", "kichaa", "mjinga", "nonsense"]): return "Tafadhali tumia lugha nzuri ili nipate kukusaidia kwa heshima."

payload = {"inputs": question}
try:
    response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=20)
    if response.status_code == 200:
        return response.json()[0]['generated_text']
    else:
        return "Samahani, siwezi kupata jibu kwa sasa. Tafadhali jaribu tena."
except Exception as e:
    logger.error(f"Error during HuggingFace API call: {e}")
    return "Kuna tatizo la kiufundi."

/start command

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: user = update.effective_user lang_prompt = "Hello! / Hujambo!\nChoose your language:\n1. English\n2. Kiswahili" await update.message.reply_text(lang_prompt)

AI Response

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: user_message = update.message.text response = await ask_ai(user_message) await update.message.reply_text(response)

Main

def main(): TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

app.run_polling()

if name == "main": main()

requirements.txt

These are the dependencies for the bot

Save this as requirements.txt in the same repo

#-----------------------------------------------

python-telegram-bot v20+ uses asyncio

python-telegram-bot==20.6 requests==2.31.0

render.yaml

This file tells Render how to deploy your Telegram bot

Save it as render.yaml in your repo root

#----------------------------------------------- services:

type: web name: ai-rafiki-bot env: python plan: free buildCommand: "pip install -r requirements.txt" startCommand: "python bot.py" envVars:

key: TELEGRAM_TOKEN sync: false

key: HUGGINGFACE_TOKEN sync: false



