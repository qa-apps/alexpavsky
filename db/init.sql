-- ============================================================
-- alexpavsky.com — PostgreSQL schema
-- Migrated from SQLite (chat_server.py _init_db)
-- Run automatically on first docker-compose up via
--   /docker-entrypoint-initdb.d/init.sql
-- ============================================================

-- Extensions ---------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram search for full-text
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid()

-- ============================================================
-- EXISTING TABLES (migrated from SQLite)
-- ============================================================

CREATE TABLE IF NOT EXISTS chat_logs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT        NOT NULL,
    ip_hash     TEXT,
    role        TEXT        NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    message     TEXT        NOT NULL,
    model       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS subscribers (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        UNIQUE NOT NULL,
    ip_hash     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    email           TEXT        UNIQUE NOT NULL,
    password_hash   TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT        PRIMARY KEY,
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
);

CREATE TABLE IF NOT EXISTS forum_posts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    user_name   TEXT        NOT NULL,
    text        TEXT        NOT NULL,
    parent_id   UUID        REFERENCES forum_posts(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    sender      TEXT        NOT NULL,
    text        TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS newsletter_runs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    week_key         TEXT        NOT NULL,
    run_type         TEXT        NOT NULL,
    status           TEXT        NOT NULL,
    subject          TEXT,
    article_count    INT         NOT NULL DEFAULT 0,
    subscriber_count INT         NOT NULL DEFAULT 0,
    sent_count       INT         NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS newsletter_deliveries (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID        NOT NULL REFERENCES newsletter_runs(id) ON DELETE CASCADE,
    email       TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS password_resets (
    token       TEXT        PRIMARY KEY,
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used        BOOLEAN     NOT NULL DEFAULT FALSE
);

-- ============================================================
-- NEW TABLES (RAG / eval infrastructure)
-- ============================================================

CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    filename    TEXT        NOT NULL,
    content     TEXT,                       -- full raw text (optional cache)
    chunk_count INT         NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    embedding       vector(1536),           -- text-embedding-3-small output dim
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS rag_queries (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        REFERENCES users(id) ON DELETE SET NULL,
    session_id          TEXT,
    query               TEXT        NOT NULL,
    answer              TEXT        NOT NULL,
    model               TEXT,
    faithfulness        FLOAT,              -- 0-1: answer grounded in context
    answer_relevancy    FLOAT,              -- 0-1: cosine sim query↔answer embed
    context_precision   FLOAT,              -- 0-1: retrieved chunks relevant
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
    test_name   TEXT,
    model       TEXT        NOT NULL,
    prompt      TEXT        NOT NULL,
    expected    TEXT,
    actual      TEXT        NOT NULL,
    score       FLOAT,                      -- 0-1
    metric      TEXT        NOT NULL,       -- exact_match | contains | llm_judge
    passed      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- ============================================================

-- pgvector ANN index (IVFFlat, cosine distance)
-- NOTE: IVFFlat requires data to exist before CREATE INDEX.
--       On an empty table this still succeeds; rebuild after bulk load:
--         REINDEX INDEX idx_chunks_embedding;
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- General indexes
CREATE INDEX IF NOT EXISTS idx_chat_logs_created_at  ON chat_logs (created_at);
CREATE INDEX IF NOT EXISTS idx_chat_logs_session     ON chat_logs (session_id);
CREATE INDEX IF NOT EXISTS idx_users_email           ON users (email);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id      ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires      ON sessions (expires_at);
CREATE INDEX IF NOT EXISTS idx_newsletter_runs_week  ON newsletter_runs (week_key, run_type, status);
CREATE INDEX IF NOT EXISTS idx_rag_queries_session   ON rag_queries (session_id);
CREATE INDEX IF NOT EXISTS idx_rag_queries_created   ON rag_queries (created_at);
CREATE INDEX IF NOT EXISTS idx_eval_runs_created     ON eval_runs (created_at);
CREATE INDEX IF NOT EXISTS idx_nl_deliveries_run     ON newsletter_deliveries (run_id);

-- Trigram index for full-text search on chat messages
CREATE INDEX IF NOT EXISTS idx_chat_logs_message_trgm
    ON chat_logs USING gin (message gin_trgm_ops);

-- Convenience views for pgAdmin.
CREATE OR REPLACE VIEW recent_users AS
SELECT id, name, email, created_at
FROM users
ORDER BY created_at DESC;

CREATE OR REPLACE VIEW recent_chat_logs AS
SELECT created_at, session_id, role, model, message
FROM chat_logs
ORDER BY created_at DESC;

-- ============================================================
-- AUTO-CLEANUP (90-day log retention)
-- ============================================================
-- To enable automatic cleanup, install pg_cron and add:
--
--   CREATE EXTENSION IF NOT EXISTS pg_cron;
--   SELECT cron.schedule(
--       'cleanup-old-chat-logs',
--       '0 3 * * *',          -- 03:00 UTC daily
--       $$DELETE FROM chat_logs WHERE created_at < NOW() - INTERVAL '90 days';$$
--   );
--
-- Or schedule via system cron (see ops/cleanup_logs.sql):
--   0 3 * * * psql $DATABASE_URL -f /path/to/ops/cleanup_logs.sql >> /var/log/cleanup.log 2>&1
-- ============================================================
