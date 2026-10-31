"""Initial schema: users, sessions, budgets, months, groups, categories, allocations, transactions.

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Keep in sync with app.money.MAX_CENTS
MAXC = 10**11

UPGRADE = f"""
CREATE TABLE users (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL,
    display_name  text,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_email_normalized CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    CONSTRAINT users_display_name_len CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 80)
);
CREATE UNIQUE INDEX users_email_key ON users (email);

-- Server-managed sessions. Only a SHA-256 of the cookie token is stored.
CREATE TABLE sessions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash          text NOT NULL UNIQUE,
    created_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    expires_at          timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_expires_idx ON sessions (expires_at);

-- Attempted auth events for rate limiting. bucket holds keyed hashes only.
CREATE TABLE auth_attempts (
    id         bigserial PRIMARY KEY,
    bucket     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX auth_attempts_bucket_idx ON auth_attempts (bucket, created_at);

-- One budget per user (unique user_id). Also the per-budget write lock row.
CREATE TABLE budgets (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name       text NOT NULL DEFAULT 'My budget',
    currency   char(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Created lazily the first time a month receives an assignment.
CREATE TABLE budget_months (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id  uuid NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    month      date NOT NULL CHECK (month = date_trunc('month', month)::date),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (budget_id, month),
    UNIQUE (id, budget_id)
);

CREATE TABLE category_groups (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id  uuid NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    name       text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, budget_id)
);
CREATE UNIQUE INDEX category_groups_name_key ON category_groups (budget_id, lower(name));

CREATE TABLE categories (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id   uuid NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    group_id    uuid NOT NULL,
    name        text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 60),
    target_text text CHECK (target_text IS NULL OR length(target_text) <= 80),
    sort_order  integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, budget_id),
    FOREIGN KEY (group_id, budget_id) REFERENCES category_groups (id, budget_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX categories_name_key ON categories (budget_id, lower(name));
CREATE INDEX categories_group_idx ON categories (group_id);

-- assigned_cents per (category, month). Composite FKs guarantee all rows share one budget.
CREATE TABLE allocations (
    category_id     uuid NOT NULL,
    budget_month_id uuid NOT NULL,
    budget_id       uuid NOT NULL,
    assigned_cents  bigint NOT NULL DEFAULT 0 CHECK (assigned_cents BETWEEN -{MAXC} AND {MAXC}),
    version         integer NOT NULL DEFAULT 1,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (category_id, budget_month_id),
    FOREIGN KEY (category_id, budget_id) REFERENCES categories (id, budget_id) ON DELETE CASCADE,
    FOREIGN KEY (budget_month_id, budget_id) REFERENCES budget_months (id, budget_id) ON DELETE CASCADE
);
CREATE INDEX allocations_budget_idx ON allocations (budget_id);

-- amount_cents: negative = outflow, positive = inflow. category_id NULL = income (must be positive).
CREATE TABLE transactions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id    uuid NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    category_id  uuid,
    occurred_on  date NOT NULL CHECK (occurred_on BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'),
    payee        text NOT NULL CHECK (length(btrim(payee)) BETWEEN 1 AND 120),
    memo         text CHECK (memo IS NULL OR length(memo) <= 500),
    amount_cents bigint NOT NULL CHECK (amount_cents <> 0 AND amount_cents BETWEEN -{MAXC} AND {MAXC}),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT transactions_income_positive CHECK (category_id IS NOT NULL OR amount_cents > 0),
    FOREIGN KEY (category_id, budget_id) REFERENCES categories (id, budget_id) ON DELETE RESTRICT
);
CREATE INDEX transactions_budget_date_idx ON transactions (budget_id, occurred_on DESC, created_at DESC, id DESC);
CREATE INDEX transactions_category_idx ON transactions (category_id, occurred_on);
"""

DOWNGRADE = """
DROP TABLE IF EXISTS transactions, allocations, categories, category_groups,
    budget_months, budgets, auth_attempts, sessions, users CASCADE;
"""


def _statements(sql: str):
    return [s.strip() for s in sql.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(UPGRADE):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _statements(DOWNGRADE):
        op.execute(stmt)
