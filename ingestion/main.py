from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from schemas import BatchIn, LogIn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingestion")

STREAM_KEY = os.getenv("LOG_STREAM", "llm.logs")
GROUP = os.getenv("LOG_GROUP", "ingestion-workers")
CONSUMER = os.getenv("HOSTNAME", "consumer-1")
ENABLE_STREAM_CONSUMER = os.getenv("ENABLE_STREAM_CONSUMER", "0") == "1"
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
INGESTION_WRITE_KEY = os.getenv("INGESTION_WRITE_KEY", "")
ALLOWED_ORIGINS = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if item.strip()]

_pool: asyncpg.Pool | None = None
_redis: redis.Redis | None = None
_queue: asyncio.Queue[LogIn] = asyncio.Queue(maxsize=20_000)
_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _redis
    _pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=2, max_size=10)
    _redis = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"), decode_responses=True)
    _tasks.append(asyncio.create_task(_writer(), name="writer"))
    if ENABLE_STREAM_CONSUMER:
        try:
            await _redis.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        _tasks.append(asyncio.create_task(_stream_consumer(), name="stream-consumer"))
    yield
    for task in _tasks:
        task.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    await _pool.close()
    await _redis.aclose()


app = FastAPI(title="Ingestion", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Ingestion-Key"],
)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.url.path == "/healthz" or request.method == "OPTIONS":
        return await call_next(request)
    if request.url.path == "/v1/logs" and request.method == "POST":
        if not INGESTION_WRITE_KEY:
            return JSONResponse(status_code=503, content={"detail": "Ingestion write key is not configured"})
        if not hmac.compare_digest(request.headers.get("x-ingestion-key", ""), INGESTION_WRITE_KEY):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)
    if not API_AUTH_TOKEN:
        return JSONResponse(status_code=503, content={"detail": "API auth is not configured"})
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {API_AUTH_TOKEN}"):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.post("/v1/logs")
async def ingest(batch: BatchIn):
    accepted = 0
    for record in batch.logs:
        try:
            _queue.put_nowait(record)
            accepted += 1
            if _redis is not None:
                await _redis.xadd(STREAM_KEY, {"json": record.model_dump_json()}, maxlen=100_000, approximate=True)
        except asyncio.QueueFull:
            raise HTTPException(503, "ingestion backpressure")
    return {"accepted": accepted}


@app.get("/v1/stats")
async def stats(window_minutes: int = Query(default=60, ge=1, le=1440)):
    assert _pool is not None
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date_trunc('minute', started_at) AS bucket, provider, model,
                   COUNT(*) AS calls,
                   COUNT(*) FILTER (WHERE status='error') AS errors,
                   COUNT(*) FILTER (WHERE status='cancelled') AS cancelled,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                   percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                   percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms) AS p99_ms,
                   SUM(total_tokens) AS tokens
            FROM inference_logs
            WHERE started_at >= NOW() - ($1 || ' minutes')::interval
            GROUP BY bucket, provider, model
            ORDER BY bucket
            """,
            str(window_minutes),
        )
    return {"window_minutes": window_minutes, "rows": [dict(row) | {"bucket": row["bucket"].isoformat()} for row in rows]}


@app.get("/v1/logs")
async def logs(limit: int = Query(default=50, ge=1, le=200)):
    assert _pool is not None
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT request_id, provider, model, status, latency_ms, ttft_ms, prompt_tokens, completion_tokens, "
            "total_tokens, error_message, input_preview, output_preview, started_at "
            "FROM inference_logs ORDER BY started_at DESC LIMIT $1",
            limit,
        )
    return [dict(row) | {"started_at": row["started_at"].isoformat()} for row in rows]


@app.get("/healthz")
async def healthz():
    return {"ok": True}


async def _writer() -> None:
    while True:
        try:
            await _persist(await _queue.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("writer dropped record")


async def _persist(record: LogIn) -> None:
    assert _pool is not None
    started = datetime.fromtimestamp(record.started_at, tz=timezone.utc)
    completed = datetime.fromtimestamp(record.completed_at, tz=timezone.utc) if record.completed_at else None
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO inference_logs (
              request_id, conversation_id, message_id, provider, model, status, error_message,
              latency_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens,
              input_preview, output_preview, streamed, pii_redacted, metadata, started_at, completed_at
            ) VALUES (
              $1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12,
              $13, $14, $15, $16, $17::jsonb, $18, $19
            )
            ON CONFLICT (request_id) DO UPDATE SET
              status=EXCLUDED.status,
              error_message=EXCLUDED.error_message,
              latency_ms=COALESCE(EXCLUDED.latency_ms, inference_logs.latency_ms),
              ttft_ms=COALESCE(EXCLUDED.ttft_ms, inference_logs.ttft_ms),
              prompt_tokens=COALESCE(EXCLUDED.prompt_tokens, inference_logs.prompt_tokens),
              completion_tokens=COALESCE(EXCLUDED.completion_tokens, inference_logs.completion_tokens),
              total_tokens=COALESCE(EXCLUDED.total_tokens, inference_logs.total_tokens),
              output_preview=COALESCE(EXCLUDED.output_preview, inference_logs.output_preview),
              completed_at=COALESCE(EXCLUDED.completed_at, inference_logs.completed_at),
              metadata=inference_logs.metadata || EXCLUDED.metadata
            """,
            record.request_id, record.conversation_id, record.message_id, record.provider, record.model,
            record.status, record.error_message, record.latency_ms, record.ttft_ms, record.prompt_tokens,
            record.completion_tokens, record.total_tokens, record.input_preview, record.output_preview,
            record.streamed, record.pii_redacted, json.dumps(record.metadata), started, completed,
        )


async def _stream_consumer() -> None:
    assert _redis is not None
    while True:
        try:
            response = await _redis.xreadgroup(GROUP, CONSUMER, {STREAM_KEY: ">"}, count=64, block=2000)
            for _stream, entries in response or []:
                for entry_id, fields in entries:
                    await _queue.put(LogIn.model_validate_json(fields["json"]))
                    await _redis.xack(STREAM_KEY, GROUP, entry_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stream consumer retrying")
            await asyncio.sleep(1)
