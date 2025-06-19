import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Logging kwa ufuatiliaji wa errors na status
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Command handler ya /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I'm alive and working 🤖")

# Kupata token kutoka kwa environment variable
BOT_TOKEN = os.getenv("BOT_TOKEN")

if BOT_TOKEN is None:
    raise ValueError("BOT_TOKEN is not set in environment variables!")

# Kuanzisha bot
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Kuongeza handler ya /start
app.add_handler(CommandHandler("start", start))

# Kuwasha bot
if __name__ == "__main__":
    app.run_polling()
