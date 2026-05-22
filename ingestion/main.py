"""Ingestion service.

Two ingress paths share one writer:
  - HTTP POST /v1/logs (used by the SDK's direct flusher)
  - Redis Stream consumer at `llm.logs` (event-based fan-out)

Both feed records into a single asyncio.Queue that a writer task drains
into Postgres with an UPSERT keyed on request_id (idempotent retries)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import BatchIn, LogIn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingestion")

STREAM_KEY = os.getenv("LOG_STREAM", "llm.logs")
GROUP = os.getenv("LOG_GROUP", "ingestion-workers")
CONSUMER = os.getenv("HOSTNAME", "consumer-1")

_pool: asyncpg.Pool | None = None
_redis: redis.Redis | None = None
_queue: asyncio.Queue[LogIn] = asyncio.Queue(maxsize=20_000)
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _redis
    _pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=2, max_size=10)
    _redis = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"), decode_responses=True)
    try:
        await _redis.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    _tasks.append(asyncio.create_task(_writer(), name="writer"))
    _tasks.append(asyncio.create_task(_stream_consumer(), name="stream-consumer"))
    log.info("ingestion ready")
    yield
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    await _pool.close()
    await _redis.aclose()


app = FastAPI(title="Ingestion", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

@app.post("/v1/logs")
async def ingest(batch: BatchIn):
    accepted = 0
    for rec in batch.logs:
        try:
            _queue.put_nowait(rec)
            accepted += 1
            if _redis is not None:
                # Republish on the stream so other subscribers (dashboards,
                # alerting, archival) can consume the same event.
                await _redis.xadd(STREAM_KEY, {"json": rec.model_dump_json()}, maxlen=100_000, approximate=True)
        except asyncio.QueueFull:
            raise HTTPException(503, "ingestion backpressure")
    return {"accepted": accepted}


@app.get("/v1/stats")
async def stats(window_minutes: int = 60):
    assert _pool is not None
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              date_trunc('minute', started_at) AS bucket,
              provider,
              model,
              COUNT(*)                                                AS calls,
              COUNT(*) FILTER (WHERE status='error')                  AS errors,
              COUNT(*) FILTER (WHERE status='cancelled')              AS cancelled,
              percentile_disc(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
              percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
              percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
              SUM(total_tokens)                                       AS tokens
            FROM inference_logs
            WHERE started_at >= NOW() - ($1 || ' minutes')::interval
            GROUP BY bucket, provider, model
            ORDER BY bucket
            """,
            str(window_minutes),
        )
    return {"window_minutes": window_minutes, "rows": [dict(r) | {"bucket": r["bucket"].isoformat()} for r in rows]}


@app.get("/v1/logs")
async def recent_logs(limit: int = 50):
    assert _pool is not None
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT request_id, provider, model, status, latency_ms, ttft_ms, "
            "prompt_tokens, completion_tokens, total_tokens, error_message, "
            "input_preview, output_preview, started_at "
            "FROM inference_logs ORDER BY started_at DESC LIMIT $1",
            limit,
        )
    return [dict(r) | {"started_at": r["started_at"].isoformat()} for r in rows]


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

async def _writer() -> None:
    assert _pool is not None
    while True:
        try:
            rec = await _queue.get()
            await _persist(rec)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("writer failed; record dropped")


async def _persist(rec: LogIn) -> None:
    assert _pool is not None
    started = datetime.fromtimestamp(rec.started_at, tz=timezone.utc)
    completed = datetime.fromtimestamp(rec.completed_at, tz=timezone.utc) if rec.completed_at else None
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO inference_logs (
              request_id, conversation_id, message_id, provider, model, status,
              error_message, latency_ms, ttft_ms, prompt_tokens, completion_tokens,
              total_tokens, input_preview, output_preview, streamed, pii_redacted,
              metadata, started_at, completed_at
            ) VALUES (
              $1, $2::uuid, $3::uuid, $4, $5, $6,
              $7, $8, $9, $10, $11,
              $12, $13, $14, $15, $16,
              $17::jsonb, $18, $19
            )
            ON CONFLICT (request_id) DO UPDATE SET
              status=EXCLUDED.status,
              error_message=EXCLUDED.error_message,
              latency_ms=COALESCE(EXCLUDED.latency_ms, inference_logs.latency_ms),
              ttft_ms=COALESCE(EXCLUDED.ttft_ms, inference_logs.ttft_ms),
              prompt_tokens=COALESCE(EXCLUDED.prompt_tokens, inference_logs.prompt_tokens),
              completion_tokens=COALESCE(EXCLUDED.completion_tokens, inference_logs.completion_tokens),
              total_tokens=COALESCE(EXCLUDED.total_tokens, inference_logs.total_tokens),
              input_preview=COALESCE(EXCLUDED.input_preview, inference_logs.input_preview),
              output_preview=COALESCE(EXCLUDED.output_preview, inference_logs.output_preview),
              completed_at=COALESCE(EXCLUDED.completed_at, inference_logs.completed_at),
              metadata=inference_logs.metadata || EXCLUDED.metadata
            """,
            rec.request_id, rec.conversation_id, rec.message_id, rec.provider,
            rec.model, rec.status, rec.error_message, rec.latency_ms, rec.ttft_ms,
            rec.prompt_tokens, rec.completion_tokens, rec.total_tokens,
            rec.input_preview, rec.output_preview, rec.streamed, rec.pii_redacted,
            json.dumps(rec.metadata), started, completed,
        )


async def _stream_consumer() -> None:
    assert _redis is not None
    while True:
        try:
            resp = await _redis.xreadgroup(
                GROUP, CONSUMER, {STREAM_KEY: ">"}, count=64, block=2000,
            )
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    try:
                        rec = LogIn.model_validate_json(fields["json"])
                        await _queue.put(rec)
                        await _redis.xack(STREAM_KEY, GROUP, entry_id)
                    except Exception:
                        log.exception("bad stream entry %s", entry_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stream consumer crashed; retrying")
            await asyncio.sleep(1)
