"""
Telegram Premium Bot — production-ready single-file template
File: telegram_premium_bot_production.py
Language: Python (3.13+)
"""
import os
import asyncio
import logging
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import openai
import stripe
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
import uvicorn

# ---------- Configuration ----------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("premium_bot")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
STRIPE_SECRET = os.environ.get("STRIPE_SECRET")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
PREMIUM_PRICE_ID = os.environ.get("PREMIUM_PRICE_ID")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID") or 0)
MAX_MONTHLY_TOKENS = int(os.environ.get("MAX_MONTHLY_TOKENS") or 3_000_000)
PRICE_USD = float(os.environ.get("PRICE_USD") or 5.0)

if not (TELEGRAM_TOKEN and OPENAI_API_KEY and DATABASE_URL and STRIPE_SECRET and STRIPE_WEBHOOK_SECRET):
    log.warning("One or more critical environment variables are missing. Fill TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, DATABASE_URL, STRIPE_SECRET, STRIPE_WEBHOOK_SECRET before production.")

openai.api_key = OPENAI_API_KEY
stripe.api_key = STRIPE_SECRET

# ---------- DB helpers ----------
async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            is_premium BOOLEAN DEFAULT FALSE,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            monthly_tokens_used BIGINT DEFAULT 0,
            monthly_reset TIMESTAMP WITH TIME ZONE DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS token_events (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            tokens_used BIGINT,
            prompt_hash TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
        );
        """)

async def get_user(conn, user_id: int):
    row = await conn.fetchrow('SELECT * FROM users WHERE id=$1', user_id)
    return row

async def ensure_user(conn, user_id: int):
    await conn.execute('INSERT INTO users(id) VALUES($1) ON CONFLICT DO NOTHING', user_id)

async def set_premium(conn, user_id: int, is_premium: bool, stripe_customer_id: Optional[str]=None, stripe_sub_id: Optional[str]=None):
    await conn.execute('UPDATE users SET is_premium=$2, stripe_customer_id=$3, stripe_subscription_id=$4 WHERE id=$1', user_id, is_premium, stripe_customer_id, stripe_sub_id)

async def add_tokens_used(conn, user_id: int, tokens: int, prompt_hash: Optional[str]=None):
    await conn.execute('INSERT INTO token_events(user_id, tokens_used, prompt_hash) VALUES($1,$2,$3)', user_id, tokens, prompt_hash)
    await conn.execute('UPDATE users SET monthly_tokens_used = monthly_tokens_used + $2 WHERE id=$1', user_id, tokens)

async def reset_monthly_if_needed(conn, user_id: int):
    row = await conn.fetchrow('SELECT monthly_reset FROM users WHERE id=$1', user_id)
    if not row:
        return
    reset_at = row['monthly_reset']
    now = datetime.now(timezone.utc)
    if now >= (reset_at + timedelta(days=30)):
        await conn.execute('UPDATE users SET monthly_tokens_used=0, monthly_reset=$2 WHERE id=$1', user_id, now)

# ---------- OpenAI wrapper (usage accounting) ----------
async def ask_openai_and_account(pool, user_id: int, prompt: str):
    model = 'gpt-4o-mini'  # change as needed
    response = await asyncio.to_thread(lambda: openai.ChatCompletion.create(
        model=model,
        messages=[{"role":"user","content":prompt}],
        max_tokens=700
    ))
    usage = response.get('usage', {})
    total_tokens = usage.get('total_tokens') or 0
    async with pool.acquire() as conn:
        await add_tokens_used(conn, user_id, total_tokens)
    text = ''
    for ch in response.get('choices', []):
        text += ch.get('message', {}).get('content', '')
    return text, total_tokens

# ---------- Telegram bot (aiogram) ----------
bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None
dp = Dispatcher()

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    pool = message.bot.get('db_pool')
    async with pool.acquire() as conn:
        await ensure_user(conn, message.from_user.id)
    text = (
        "Привіт! Це бот з преміум-підпискою.\n"
        "Преміум — $5/міс. Натисни /buy щоб оформити підписку.\n"
        "Якщо у тебе вже є підписка — напиши будь-яке питання і отримаєш відповідь від GPT."
    )
    await message.reply(text)

@dp.message(Command('buy'))
async def cmd_buy(message: types.Message):
    await message.reply('Щоб купити підписку — відкрий посилання на Checkout (створіть Checkout session на вашому сайті і передайте metadata.telegram_user_id).')

@dp.message()
async def handle_message(message: types.Message):
    pool = message.bot.get('db_pool')
    async with pool.acquire() as conn:
        await ensure_user(conn, message.from_user.id)
        await reset_monthly_if_needed(conn, message.from_user.id)
        user = await get_user(conn, message.from_user.id)
        if not user['is_premium']:
            await message.reply('У тебе немає активної преміум-підписки. /buy')
            return
        if user['monthly_tokens_used'] >= MAX_MONTHLY_TOKENS:
            await message.reply('Ви досягли місячного ліміту токенів. Зверніться до адміністратора.')
            return
    await message.chat.do('typing')
    text = message.text or message.caption or ''
    try:
        answer, tokens = await ask_openai_and_account(message.bot.get('db_pool'), message.from_user.id, text)
        await message.reply(answer)
    except Exception as e:
        log.exception('OpenAI failed')
        await message.reply('Помилка при зверненні до сервісу. Спробуйте пізніше.')

# ---------- FastAPI for Stripe webhooks and admin endpoints ----------
app = FastAPI()

@app.post('/stripe/webhook')
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        log.exception('Webhook signature failed')
        raise HTTPException(status_code=400, detail=str(e))

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        user_id = session.get('metadata', {}).get('telegram_user_id')
        customer = session.get('customer')
        subscription = session.get('subscription')
        if user_id:
            async with app.state.db_pool.acquire() as conn:
                await ensure_user(conn, int(user_id))
                await set_premium(conn, int(user_id), True, stripe_customer_id=customer, stripe_sub_id=subscription)
                log.info(f'Set premium for user {user_id} via checkout.session.completed')
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        sub_id = invoice.get('subscription')
        async with app.state.db_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id FROM users WHERE stripe_subscription_id=$1', sub_id)
            if row:
                await set_premium(conn, row['id'], False)
                log.info(f'Disabled premium for user {row["id"]} due to payment failure')
    elif event['type'] == 'customer.subscription.deleted':
        sub = event['data']['object']
        sub_id = sub.get('id')
        async with app.state.db_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id FROM users WHERE stripe_subscription_id=$1', sub_id)
            if row:
                await set_premium(conn, row['id'], False)
                log.info(f'Cancelled subscription for user {row["id"]}')

    return PlainTextResponse('ok')

@app.on_event('startup')
async def startup_event():
    log.info('Starting up — connecting to DB')
    app.state.db_pool = await asyncpg.create_pool(DATABASE_URL)
    await init_db(app.state.db_pool)

@app.on_event('shutdown')
async def shutdown_event():
    log.info('Shutting down — closing DB')
    await app.state.db_pool.close()

# ---------- Runner ----------
async def run_bot(pool):
    bot['db_pool'] = pool
    await dp.start_polling(bot)

async def main_worker():
    pool = await asyncpg.create_pool(DATABASE_URL)
    await init_db(pool)
    app.state.db_pool = pool
    bot_task = asyncio.create_task(run_bot(pool))
    config = uvicorn.Config(app, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), log_level='info')
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve())
    await asyncio.gather(bot_task, api_task)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true', help='Run only polling worker (no FastAPI)')
    args = parser.parse_args()
    if args.worker:
        async def run_polling_only():
            pool = await asyncpg.create_pool(DATABASE_URL)
            await init_db(pool)
            bot['db_pool'] = pool
            await dp.start_polling(bot)
        asyncio.run(run_polling_only())
    else:
        asyncio.run(main_worker())
