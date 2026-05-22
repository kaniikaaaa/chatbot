# Architecture Notes

## Components

| Service        | Stack                  | Responsibility                                                                 |
|----------------|------------------------|---------------------------------------------------------------------------------|
| `frontend`     | Next.js 14, Tailwind   | Chat UI (stream, cancel, list, resume), dashboards, log viewer                  |
| `chatbot-api`  | FastAPI, asyncpg       | Conversation/message CRUD, chat (SSE), context window, cancel signalling        |
| `llm-sdk`      | Python pkg             | Provider adapters (OpenAI, Anthropic, Gemini), timing, PII redact, log shipper |
| `ingestion`    | FastAPI, redis-py      | HTTP ingest, Redis Stream republish + consumer, Postgres writer, stats API     |
| `postgres`     | Postgres 16            | Conversations, messages, inference_logs (JSONB metadata)                       |
| `redis`        | Redis 7                | `llm.logs` stream / event bus + cross-replica signalling                       |

## Ingestion flow

```
  user → frontend → chatbot-api → llm-sdk → provider
                                    │
                                    ├── LogRecord enqueued
                                    │
                                    ▼ (async batch)
                              POST /v1/logs
                                    │
                              ┌─────┴─────┐
                              ▼           ▼
                      Postgres writer   Redis Stream (XADD)
                              │           │
                              │           ▼
                              │     other consumers
                              ▼     (alerts, BI, …)
                          inference_logs
```

- The SDK enqueues `LogRecord` objects to an in-process `asyncio.Queue` and flushes them in micro-batches (every queue-drain cycle, max 100) to the ingestion endpoint with retry + backoff.
- The ingestion service is dual-mode: each accepted record is (a) put on a writer queue that upserts into Postgres and (b) `XADD`-ed to the `llm.logs` Redis Stream with `MAXLEN ~ 100000` so subscribers can fan-out without re-querying the DB.
- A consumer group `ingestion-workers` on the same stream lets you scale horizontally — adding replicas splits the partition automatically.

## Logging strategy

- **Non-blocking**: never let log shipping back-pressure the chat path. The SDK queue is bounded; overflow drops with a warning rather than blocking the model.
- **Idempotent**: each log carries a `request_id`; the writer uses `INSERT … ON CONFLICT (request_id) DO UPDATE` so retries (or out-of-order completion updates from streaming) merge instead of duplicating.
- **Forward-compatible**: a JSONB `metadata` column accepts arbitrary provider-specific fields without requiring schema migrations.
- **Privacy-aware**: previews capped at 500 chars and run through a regex PII pass before persistence (configurable with `PII_REDACT`).

## Scaling considerations

- All services are stateless (chatbot-api keeps only short-lived `asyncio.Event`s for in-flight stream cancels; that signalling moves to Redis pub/sub for multi-replica deployments).
- Postgres is the only stateful tier. The hot table (`inference_logs`) can be range-partitioned on `started_at` (monthly) once it exceeds ~10M rows.
- For very high QPS, the synchronous HTTP ingest can be bypassed: the SDK writes directly to Redis Stream, ingestion only consumes (single write path, lower fan-out cost).
- Dashboards query pre-aggregated views (`/v1/stats` returns `percentile_disc` over a date_trunc'd window) so the UI scales with retention, not call volume.

## Failure handling assumptions

| Failure                  | Behaviour                                                                                  |
|--------------------------|--------------------------------------------------------------------------------------------|
| Provider API down/error  | `status='error'` + exception in `error_message`; UI receives `error` SSE event              |
| Ingestion service down   | SDK retries 3× with exponential backoff, then drops batch and warns; chat path continues   |
| Postgres down            | Writer task surfaces error → record dropped; Redis Stream retains entries (capped) for fan-out |
| Redis down               | HTTP ingest still writes to Postgres; event-bus republish fails and is logged              |
| User cancels mid-stream  | Event flagged → SDK closes provider stream, log written with `status='cancelled'`, partial output preview retained |
| Chatbot-api crash mid-stream | The conversation's assistant message is whatever was committed; the in-flight log row is upserted with the partial state once the SDK retries |

## Frontend behaviours

- **Cancel a conversation**: `POST /v1/conversations/{id}/cancel` flips status and signals the in-flight stream via an `AbortController` + an in-memory event on the API.
- **List conversations**: `GET /v1/conversations`, ordered by `updated_at DESC`.
- **Resume a conversation**: clicking a conversation loads its full message history (`/messages`); new messages append, and a `cancelled` conversation auto-flips to `active` on the next user turn.

## k8s

`k8s/` contains a Kustomize-style flat layout (one Deployment + Service per app, one StatefulSet for Postgres, one Deployment for Redis, and an Ingress that fronts the frontend / chatbot-api / ingestion). Apply with `kubectl apply -k k8s/`.
