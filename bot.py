import os
import logging
from datetime import date
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from db import Database

# ---------- Налаштування через ENV ----------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
BANK_LINK = os.environ.get("BANK_LINK")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

db = Database()
app = FastAPI()
logging.basicConfig(level=logging.INFO)

# ---------- Telegram Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await db.add_user(user_id)
    await update.message.reply_text(
        "Привіт! У вас є 3 безкоштовних запити на день. "
        "Щоб отримати необмежений доступ, зробіть пожертву у Monobank банку та надішліть квитанцію адміну."
    )

async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await db.get_user(user_id)

    if user["last_request_date"] != date.today():
        await db.reset_daily_requests(user_id)
        user = await db.get_user(user_id)

    if not user["is_premium"] and user["free_requests_today"] >= 3:
        await update.message.reply_text(
            f"Ви використали всі безкоштовні запити на сьогодні. "
            f"Щоб отримати необмежений доступ, пожертвуйте тут: {BANK_LINK}"
        )
        return

    # --- Виклик OpenAI ---
    response = f"Тут буде відповідь OpenAI на: {update.message.text}"
    await update.message.reply_text(response)

    if not user["is_premium"]:
        await db.increment_daily_requests(user_id)

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Щоб стати Premium, зробіть пожертву тут:\n{BANK_LINK}\n"
        "Після оплати надішліть квитанцію адміну, і протягом 5 хв преміум буде активовано."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка будь-яких текстових повідомлень"""
    user_id = update.effective_user.id
    user = await db.get_user(user_id)

    if user["last_request_date"] != date.today():
        await db.reset_daily_requests(user_id)
        user = await db.get_user(user_id)

    if not user["is_premium"] and user["free_requests_today"] >= 3:
        await update.message.reply_text(
            f"Ви використали всі безкоштовні запити на сьогодні. "
            f"Щоб отримати необмежений доступ, пожертвуйте тут: {BANK_LINK}"
        )
        return

    # --- Виклик OpenAI ---
    response = f"Тут буде відповідь OpenAI на: {update.message.text}"
    await update.message.reply_text(response)

    if not user["is_premium"]:
        await db.increment_daily_requests(user_id)

# ---------- Telegram Application (створюється один раз) ----------
app_telegram = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("ask", ask))
app_telegram.add_handler(CommandHandler("buy", buy))
app_telegram.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ---------- FastAPI startup ----------
@app.on_event("startup")
async def startup():
    await db.connect()

# ---------- FastAPI endpoint для Telegram ----------
@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data)
    await app_telegram.process_update(update)
    return {"status": "ok"}
