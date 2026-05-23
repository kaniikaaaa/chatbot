# Inference Logging Chatbot

Lightweight multi-provider chatbot with real-time inference logging, ingestion, and dashboarding.

This repository implements the full core task:
- Multi-turn chatbot with short context memory and UI
- SDK wrapper around provider calls with structured inference telemetry
- Ingestion API with validation/parsing and metadata extraction
- Database persistence for conversations, messages, and inference logs

## Quick Start

### 1) Environment

```bash
cp .env.example .env
```

Required values for local run:
- `OPENAI_API_KEY` (for validated provider smoke tests)
- `API_AUTH_TOKEN`
- `INGESTION_WRITE_KEY`
- `ALLOWED_ORIGINS` (default: `http://localhost:3000`)

Optional provider keys:
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`

### 2) Run with Docker Compose

```bash
docker compose up --build
```

### 3) Open app

- Chat: `http://localhost:3000`
- Dashboard: `http://localhost:3000/dashboard`
- Logs: `http://localhost:3000/logs`

## Render Demo Deploy

This repo includes a one-container Render demo path:

- `Dockerfile.render`
- `render.yaml`

The Render image runs Next.js, `chatbot-api`, `ingestion`, Postgres, and Redis in one container. This is intentionally demo-oriented: Postgres and Redis data are ephemeral and may reset when the Render instance restarts.

Deploy steps:

1. Push this repo to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Fill `OPENAI_API_KEY` in the generated service environment.
4. Deploy the `inference-logger-demo` web service.

The app is served from the Render web service URL. Internal APIs run on localhost inside the same container.

## Architecture Overview

Services:
- `frontend` (Next.js + assistant-ui): chat UI, conversation list/resume/cancel, dashboard/log views, and server-side proxy routes for auth header injection.
- `chatbot-api` (FastAPI): conversation/message APIs, short context retrieval, streaming SSE chat, cancellation handling, and SDK integration.
- `llm-sdk` (Python package): provider abstraction (OpenAI/Anthropic/Gemini), metadata capture, PII redaction, async non-blocking log shipping.
- `ingestion` (FastAPI): log batch validation, normalization, idempotent persistence, and Redis stream fan-out.
- `postgres`: system of record for conversations/messages/inference logs.
- `redis`: event transport for `llm.logs` stream.

Flow summary:
1. User sends prompt from assistant-ui thread.
2. `chatbot-api` persists user message and loads a short conversation window.
3. Provider call runs through `llm-sdk`.
4. Response streams to UI (SSE) and assistant message is persisted.
5. SDK emits `LogRecord` to ingestion in near real time.
6. Ingestion validates/upserts Postgres and publishes stream event for downstream consumers.

## Schema Design Decisions

Primary tables:
- `conversations`: one row per chat session (`id`, `title`, `status`, timestamps).
- `messages`: normalized transcript rows (`conversation_id`, `role`, `content`, `created_at`).
- `inference_logs`: one row per model invocation keyed by `request_id`.

Design rationale:
- Use `request_id` as idempotency key to tolerate retries/duplicates in ingestion.
- Keep commonly queried analytics fields (`provider`, `model`, `status`, `latency_ms`, tokens) as first-class columns.
- Keep flexible extra context in `metadata JSONB` to avoid schema churn.
- Separate `messages` and `inference_logs` so chat UX and observability evolve independently.

## Practical Tradeoffs

- Authentication uses static service keys for local demo simplicity; production should move to scoped, rotated credentials.
- PII redaction uses lightweight regex heuristics; fast and low-cost, but not perfect.
- Streaming token usage may be approximate when provider usage counters are unavailable mid-stream.
- Postgres handles both transactional and dashboard queries in this version; at high scale, split OLTP and analytics paths.
- The Render deployment is a single-container demo; use Docker Compose or separate managed services for persistent environments.

## What I Would Improve With More Time

- Add OpenTelemetry traces/metrics across frontend proxy, API, SDK, and ingestion.
- Add dead-letter queue + replay tooling for ingestion failures.
- Add multi-tenant auth and per-tenant dashboard filters.
- Add k8s manifests/Helm and production-grade secrets/ingress setup.
- Add long-range retention strategy (partitioning + archival pipeline).

## Bonus Feature Status

- Multi-provider support: implemented (OpenAI/Anthropic/Gemini).
- Streaming responses: implemented (SSE).
- Dashboards (latency/throughput/errors): implemented.
- Docker Compose one-command setup: implemented.
- Event-based ingestion path: implemented (Redis Streams fan-out).
- PII redaction: implemented.
- Self-hosted k8s deploy: not included in this repo yet.
