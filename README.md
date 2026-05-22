# Inference Logging System

A lightweight inference logging and ingestion system for an LLM application. Built per the spec:

1. **Chatbot Application** — multi-turn chat UI with provider/model switcher and streaming.
2. **Lightweight SDK** — a Python wrapper around OpenAI / Anthropic / Gemini that captures inference metadata (model, provider, latency, TTFT, tokens, status, conversation/message IDs, input/output previews) and ships it asynchronously to an ingestion endpoint.
3. **Ingestion Pipeline** — a FastAPI service that accepts batches over HTTP, fan-outs onto a Redis Stream for event-based consumers, validates payloads, and writes them to Postgres with idempotent upserts.
4. **Database Storage** — Postgres schema for `conversations`, `messages`, and `inference_logs` with a `JSONB` `metadata` column for forward-compatible extras.

Bonus items completed:

- ✅ Multi-provider support (OpenAI, Anthropic, Gemini)
- ✅ Streaming responses end-to-end (SSE → UI token stream, cancellable)
- ✅ Latency / Throughput / Errors dashboard (`/dashboard`)
- ✅ Docker Compose one-command setup
- ✅ Event-based architecture (Redis Streams between SDK → ingestion → consumers)
- ✅ PII redaction (regex sweep before previews persist)
- ✅ Self-hosted k8s manifests (`k8s/`)

## Setup

```bash
cp .env.example .env
# fill in at least one provider API key
docker compose up --build
```

Then open:

- Chat UI:  http://localhost:3000
- Dashboard: http://localhost:3000/dashboard
- Logs feed: http://localhost:3000/logs
- Chatbot API: http://localhost:8000/docs
- Ingestion:  http://localhost:8001/docs

## Architecture overview

```
┌──────────┐    SSE      ┌──────────────┐  llm-sdk  ┌──────────┐
│ frontend │ ──────────► │ chatbot-api  │ ────────► │ provider │
│ (Next.js)│ ◄────────── │  (FastAPI)   │           └──────────┘
└──────────┘             │              │
                         │ async log    │  HTTP batches
                         └─────┬────────┘
                               ▼
                         ┌─────────────┐  XADD   ┌──────────────┐
                         │ ingestion   │ ──────► │ Redis Stream │
                         │ (FastAPI)   │ ◄────── │  llm.logs    │
                         └─────┬───────┘ XREAD   └──────────────┘
                               ▼
                         ┌────────────┐
                         │ Postgres   │
                         └────────────┘
```

### Ingestion flow

1. Chatbot API receives a chat request, persists the user message, builds a trailing-window context (configurable, default 12 turns), and calls the SDK.
2. The SDK times the call (and TTFT for streams), captures token counts and previews, runs PII redaction, and pushes a `LogRecord` onto an in-process queue.
3. A background task in the SDK flushes records in micro-batches to `POST /v1/logs` on the ingestion service with retry + backoff.
4. The ingestion service does two things in parallel: enqueues to its writer task (Postgres `UPSERT` on `request_id`) **and** publishes the record onto a `llm.logs` Redis Stream so other consumers can subscribe (alerting, sampling, BI export, etc.) without competing with the writer.

### Logging strategy

- The inference call path never blocks on the log shipper — the queue is bounded and overflow drops with a warning so the model stays responsive.
- Logs are keyed by `request_id`; the ingestion writer uses `INSERT … ON CONFLICT DO UPDATE` so a retried flush merges into the existing row rather than duplicating.
- Stream-of-tokens responses get a single log row whose `latency_ms`/`ttft_ms`/`completion_tokens` are filled in when the stream closes.
- Previews are truncated to 500 chars and run through a regex PII sweep (`[EMAIL]`, `[PHONE]`, `[CARD]`, `[SSN]`, `[IP]`, `[API_KEY]`) when `PII_REDACT=1`.

### Schema design decisions

- `conversations` carries lifecycle state (`active` / `cancelled` / `archived`) so the UI can list, resume, or cancel. `updated_at` is bumped on each new assistant message — that's the natural sort key for "recent chats."
- `messages` stores the raw transcript without provider knowledge. It joins to `inference_logs` via the optional `message_id` so you can reconstruct "this assistant message came from this LLM call."
- `inference_logs` is the heart of the system. Columns surface the hot query paths (latency, token usage, status) while a `JSONB metadata` column absorbs anything provider-specific (`temperature`, custom headers, future fields) without migrations. Indexed by `started_at DESC`, `(provider, model)`, `status`, plus a GIN index on `metadata`.
- `request_id` is a unique business key separate from the PK so producers can retry safely without race-conditioning on UUID generation.

### Tradeoffs made

- **Batched, fire-and-forget log shipping** (not synchronous) — keeps chat latency low but means a crash during shutdown can drop in-flight records. Mitigated with bounded retries and a final drain on app shutdown. For stricter durability, swap the in-process queue for the Redis Stream as the primary write path.
- **Regex PII redaction** — fast and dependency-free, but will miss free-form names or addresses. A real deployment would layer Presidio or a model-based redactor behind a feature flag.
- **No auth** — out of scope for the assignment, but every service is a single middleware away from JWT/OIDC.
- **One Postgres for OLTP + analytics** — fine up to a few million logs with the indexes given; beyond that, ship `inference_logs` to ClickHouse / BigQuery for the dashboards and keep Postgres for the chat data.
- **Token counts on streamed responses** use a coarse `len/4` approximation instead of running per-provider tokenizers in the hot path. Cheap and good enough for dashboards; rebuild from `output_preview` offline if you need exact accounting.

### Scaling considerations

- **Frontend**: stateless, scale horizontally behind any load balancer.
- **Chatbot API**: stateless except for per-conversation in-memory cancel events. For multi-replica deployments, move cancel signalling to Redis pub/sub (one line change).
- **Ingestion**: horizontally scalable. HTTP path is stateless. Stream consumers use a Redis consumer group (`ingestion-workers`) so adding replicas evenly splits the partition.
- **Postgres**: partition `inference_logs` by `started_at` (monthly) once volume grows; the existing indexes are partition-friendly.
- **Redis Stream** is capped at 100k entries with `MAXLEN ~`; bump or tier off to long-term storage as needed.

### Failure handling assumptions

- LLM provider errors are logged with `status='error'` and the exception string in `error_message`; the chat stream still emits an `error` event so the UI can render it.
- Ingestion downtime: SDK retries 3 × with exponential backoff, then drops the batch and logs. Chat path is unaffected.
- Postgres downtime: the ingestion writer task surfaces an exception, the record is dropped, and the next health check probe restarts the worker. The Redis Stream still holds recent entries (capped) so a fan-out consumer can re-derive state when the DB returns.
- Redis downtime: the HTTP write path continues working; only the event-bus republish fails (and is logged).

### What I'd improve with more time

- Replace the regex PII sweep with Presidio + a small ML model for entity types we care about.
- Move tokenization off the hot path into a sidecar that reads `output_preview` from the stream and backfills exact counts.
- Add per-tenant API keys and rate limits on the ingestion endpoint.
- Add OpenTelemetry traces so a single `trace_id` joins UI → chatbot-api → provider → ingestion.
- Replace the chart in the dashboard with a real charting lib (Recharts) and add Grafana over the Postgres for ops-grade dashboards.
- Idempotency keys + dead-letter queue on the ingestion writer.

## Repository layout

```
chatbot/
├── chatbot-api/        FastAPI chat service (multi-turn, SSE, cancel)
├── llm-sdk/            python package, wraps providers + ships logs
├── ingestion/          FastAPI ingest API + Redis Stream consumer
├── frontend/           Next.js 14 (App Router) + Tailwind
├── db/init.sql         Postgres schema
├── k8s/                kubernetes manifests for self-hosted deploy
├── docker-compose.yml  one-command local stack
├── ARCHITECTURE.md     deeper architecture notes
└── README.md
```

## Manual API examples

```bash
# create a conversation
curl -X POST localhost:8000/v1/conversations -H 'content-type: application/json' -d '{"title":"demo"}'

# send a (non-streaming) chat
curl -X POST localhost:8000/v1/chat -H 'content-type: application/json' \
  -d '{"conversation_id":"<UUID>","content":"hello","stream":false}'

# fetch recent logs
curl localhost:8001/v1/logs?limit=10

# 60-minute stats (per minute, per provider/model)
curl localhost:8001/v1/stats?window_minutes=60
```
