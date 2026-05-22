-- Inference Logging System – schema
-- Three logical buckets: conversations/messages (chat),
-- inference_logs (per-LLM-call telemetry), and a JSONB column for
-- provider-specific extracted metadata so the schema stays forward-compatible.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title         TEXT,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','cancelled','archived')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
    ON conversations (updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conv_created
    ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS inference_logs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id        TEXT UNIQUE NOT NULL,
    conversation_id   UUID REFERENCES conversations(id) ON DELETE SET NULL,
    message_id        UUID REFERENCES messages(id) ON DELETE SET NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('success','error','cancelled')),
    error_message     TEXT,
    latency_ms        INTEGER,
    ttft_ms           INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    input_preview     TEXT,
    output_preview    TEXT,
    streamed          BOOLEAN NOT NULL DEFAULT FALSE,
    pii_redacted      BOOLEAN NOT NULL DEFAULT FALSE,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at        TIMESTAMPTZ NOT NULL,
    completed_at      TIMESTAMPTZ,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_started_at      ON inference_logs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_conv            ON inference_logs (conversation_id);
CREATE INDEX IF NOT EXISTS idx_logs_provider_model  ON inference_logs (provider, model);
CREATE INDEX IF NOT EXISTS idx_logs_status          ON inference_logs (status);
CREATE INDEX IF NOT EXISTS idx_logs_metadata_gin    ON inference_logs USING GIN (metadata);
