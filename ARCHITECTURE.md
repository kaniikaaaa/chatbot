# Architecture Notes

## Ingestion Flow

1. UI sends a chat request to `chatbot-api` (via frontend proxy route with server-side auth injection).
2. `chatbot-api` persists the user message and loads a short message window (`CONTEXT_TURNS`) for model context.
3. `chatbot-api` calls `llm-sdk` with provider + model settings.
4. `llm-sdk` captures inference metadata and pushes a `LogRecord` into a non-blocking async logger queue.
5. Logger posts batches to ingestion `/v1/logs` using `INGESTION_WRITE_KEY`.
6. `ingestion` validates payloads, normalizes fields, and upserts into `inference_logs` by `request_id`.
7. `ingestion` also publishes the accepted record to Redis Stream `llm.logs` for downstream consumers.
8. Dashboard/log endpoints read from Postgres and are exposed through frontend proxy routes.

## Logging Strategy

- Log granularity: one record per provider invocation (`request_id` is unique idempotency key).
- Captured metadata:
  - provider/model
  - started/completed timestamps
  - latency + TTFT
  - prompt/completion/total tokens (when available)
  - status (`success`, `error`, `cancelled`)
  - conversation/message IDs
  - redacted input/output previews
  - free-form metadata JSON
- Reliability model:
  - non-blocking queue on hot path (chat is not blocked by ingestion)
  - bounded retries in logger before drop
  - ingestion upsert avoids duplicate row amplification
- Privacy model:
  - PII redaction happens before preview persistence
  - full prompt/response bodies are not stored in inference logs by default

## Scaling Considerations

- Stateless services:
  - `frontend`, `chatbot-api`, and `ingestion` are horizontally scalable.
- Deployment modes:
  - Docker Compose runs Postgres/Redis/API/frontend as separate services.
  - Render demo runs everything in one container with ephemeral Postgres/Redis for simpler review.
- Current single-node assumptions:
  - in-flight cancellation map is process-local in `chatbot-api`.
- Recommended upgrades for multi-replica production:
  - move cancellation signaling to Redis pub/sub or shared control channel
  - add worker pool for ingestion writes when ingest throughput increases
  - partition `inference_logs` by time for retention and query performance
  - add read replicas/materialized rollups for dashboard-heavy workloads
- Event architecture path:
  - Redis Streams already emits events
  - can evolve to consume-first architecture with durable consumer groups

## Failure Handling Assumptions

- Provider/API failures:
  - Non-streaming requests return `502`.
  - Streaming requests emit SSE `error`.
  - Failure still emits an inference log with `status='error'`.
- Ingestion failures:
  - Do not fail user chat response path.
  - Log records may be dropped after retry exhaustion (known tradeoff in lightweight mode).
- Redis failures:
  - Do not block core ingestion into Postgres.
  - Only event fan-out is degraded.
- Database failures:
  - Chat persistence or ingestion writes fail fast with explicit API errors.
  - No distributed transaction guarantees between chat write and ingestion write in this version.
