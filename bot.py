import os
import asyncio
from fastapi import FastAPI, Request
from db import DB
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, filters
import httpx
import openai

DATABASE_URL = os.environ.get("DATABASE_URL")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MONO_MERCHANT_TOKEN = os.environ.get("MONO_MERCHANT_TOKEN")
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL")
PRICE_UAH = int(os.environ.get("PRICE_UAH", 190))

openai.api_key = OPENAI_API_KEY

app = FastAPI()
db = DB(DATABASE_URL)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(bot, None)

# ---------------- Telegram Handlers ----------------
async def start(update: Update, context):
    user_id = update.effective_user.id
    await db.add_user(user_id)
    await update.message.reply_text("Привіт! Ви зареєстровані.")

async def buy(update: Update, context):
    user_id = update.effective_user.id
    # Генеруємо інвойс у Monobank
    async with httpx.AsyncClient() as client:
        invoice_data = {
            "amount": PRICE_UAH * 100,  # копійки
            "ccy": 980,
            "merchantToken": MONO_MERCHANT_TOKEN,
            "comment": f"Premium subscription {user_id}"
        }
        r = await client.post("https://api.monobank.ua/api/merchant/invoice/create", json=invoice_data)
        res = r.json()
        invoice_id = res.get("invoiceId")
    await db.set_invoice(user_id, invoice_id)
    await update.message.reply_text(f"Створено інвойс: {invoice_id}. Оплачуйте через Monobank.")

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("buy", buy))

# ---------------- FastAPI Telegram webhook ----------------
@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)
    await dp.process_update(update)
    return {"ok": True}

# ---------------- Monobank webhook ----------------
@app.post("/mono/webhook")
async def mono_webhook(req: Request):
    data = await req.json()
    invoice_id = data.get("invoiceId")
    status = data.get("status")
    # Шукаємо користувача по invoice_id
    async with db.pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE mono_invoice_id=$1", invoice_id)
    if user and status == "PAID":
        await db.set_premium(user["id"], True)
        await bot.send_message(user["id"], "Оплата пройшла! Ви стали Premium.")
    return {"ok": True}

# ---------------- OpenAI example ----------------
@app.post("/ask")
async def ask_openai(req: Request):
    data = await req.json()
    user_id = data.get("user_id")
    prompt = data.get("prompt")
    # Виклик OpenAI
    resp = await asyncio.to_thread(lambda: openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    ))
    tokens = resp.usage.total_tokens
    await db.log_tokens(user_id, tokens, prompt_hash=hash(prompt))
    return {"response": resp.choices[0].message.content, "tokens": tokens}

# ---------------- Startup/Shutdown ----------------
@app.on_event("startup")
async def startup():
    await db.connect()

@app.on_event("shutdown")
async def shutdown():
    await db.close()
