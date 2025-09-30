import asyncpg

class DB:
    def __init__(self, database_url):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.database_url)

    async def close(self):
        await self.pool.close()

    async def add_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users(id) VALUES($1) ON CONFLICT DO NOTHING",
                user_id
            )

    async def set_premium(self, user_id: int, is_premium=True):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_premium=$2, mono_invoice_id=NULL WHERE id=$1",
                user_id, is_premium
            )

    async def set_invoice(self, user_id: int, invoice_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET mono_invoice_id=$2 WHERE id=$1",
                user_id, invoice_id
            )

    async def log_tokens(self, user_id: int, tokens: int, prompt_hash=None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO token_events(user_id, tokens_used, prompt_hash) VALUES($1,$2,$3)",
                user_id, tokens, prompt_hash
            )
            await conn.execute(
                "UPDATE users SET monthly_tokens_used = monthly_tokens_used + $2 WHERE id=$1",
                user_id, tokens
            )

    async def get_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
