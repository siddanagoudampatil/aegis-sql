-- Aegis-SQL B2B SaaS Warehouse Schema (DuckDB)
-- Defines core relational entities with explicit foreign key constraints
-- to enable automated relational graph extraction.

CREATE TABLE IF NOT EXISTS customers (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    email VARCHAR NOT NULL UNIQUE,
    tier VARCHAR NOT NULL CHECK (tier IN ('starter', 'growth', 'enterprise')),
    status VARCHAR NOT NULL CHECK (status IN ('active', 'churned', 'suspended')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plans (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    tier VARCHAR NOT NULL CHECK (tier IN ('starter', 'growth', 'enterprise')),
    monthly_fee DECIMAL(10, 2) NOT NULL,
    billing_interval VARCHAR NOT NULL CHECK (billing_interval IN ('monthly', 'annual')),
    features_json VARCHAR
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL REFERENCES customers(id),
    plan_id VARCHAR NOT NULL REFERENCES plans(id),
    status VARCHAR NOT NULL CHECK (status IN ('active', 'canceled', 'past_due', 'paused')),
    mrr_amount DECIMAL(10, 2) NOT NULL,
    started_at TIMESTAMP NOT NULL,
    canceled_at TIMESTAMP,
    current_period_start TIMESTAMP NOT NULL,
    current_period_end TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id VARCHAR PRIMARY KEY,
    subscription_id VARCHAR NOT NULL REFERENCES subscriptions(id),
    customer_id VARCHAR NOT NULL REFERENCES customers(id),
    amount_due DECIMAL(10, 2) NOT NULL,
    amount_paid DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    status VARCHAR NOT NULL CHECK (status IN ('paid', 'open', 'void', 'uncollectible')),
    due_date DATE NOT NULL,
    paid_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_events (
    id VARCHAR PRIMARY KEY,
    customer_id VARCHAR NOT NULL REFERENCES customers(id),
    subscription_id VARCHAR NOT NULL REFERENCES subscriptions(id),
    event_type VARCHAR NOT NULL CHECK (event_type IN ('api_call', 'compute_hour', 'export_gb')),
    quantity INTEGER NOT NULL DEFAULT 1,
    recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
