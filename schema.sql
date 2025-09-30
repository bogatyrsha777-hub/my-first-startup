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
