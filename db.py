import asyncpg
from datetime import date

DATABASE_URL = "postgresql://db_1_w9vo_user:bTzggpUbr1MyJjfn3WsHbQaJgAu3U9Ug@dpg-d3drfhqdbo4c73dpkoo0-a.oregon-postgres.render.com/db_1_w9vo"

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)

    async def add_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (id) 
                VALUES ($1)
                ON CONFLICT (id) DO NOTHING;
            """, user_id)

    async def get_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                SELECT * FROM users WHERE id=$1;
            """, user_id)

    async def increment_daily_requests(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE users
                SET free_requests_today = free_requests_today + 1,
                    last_request_date = $2
                WHERE id=$1;
            """, user_id, date.today())

    async def reset_daily_requests(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE users
                SET free_requests_today = 0,
                    last_request_date = $2
                WHERE id=$1;
            """, user_id, date.today())

    async def set_premium(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE users
                SET is_premium = TRUE
                WHERE id=$1;
            """, user_id)
