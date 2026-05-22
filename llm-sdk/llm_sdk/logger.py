"""Fire-and-forget log shipper. Buffers records in an asyncio.Queue and
flushes them to the ingestion endpoint on a background task so the
inference call path never blocks on network I/O to the logger."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

log = logging.getLogger("llm_sdk.logger")


@dataclass
class LogRecord:
    request_id: str
    provider: str
    model: str
    status: str
    started_at: float
    completed_at: float | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    input_preview: str | None = None
    output_preview: str | None = None
    streamed: bool = False
    pii_redacted: bool = False
    error_message: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InferenceLogger:
    def __init__(
        self,
        endpoint: str | None = None,
        flush_interval: float = 0.25,
        max_queue: int = 10_000,
    ) -> None:
        self.endpoint = endpoint or os.getenv(
            "INGESTION_URL", "http://ingestion:8001/v1/logs"
        )
        self._queue: asyncio.Queue[LogRecord] = asyncio.Queue(maxsize=max_queue)
        self._flush_interval = flush_interval
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._client = httpx.AsyncClient(timeout=5.0)
            self._task = asyncio.create_task(self._run(), name="llm-sdk-flusher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    def log(self, record: LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            log.warning("inference log queue full — dropping record %s", record.request_id)

    async def _run(self) -> None:
        assert self._client is not None
        while True:
            try:
                batch: list[LogRecord] = [await self._queue.get()]
                # Drain whatever else is queued without waiting.
                while not self._queue.empty() and len(batch) < 100:
                    batch.append(self._queue.get_nowait())
                await self._flush(batch)
            except asyncio.CancelledError:
                # Final drain on shutdown.
                rest: list[LogRecord] = []
                while not self._queue.empty():
                    rest.append(self._queue.get_nowait())
                if rest:
                    await self._flush(rest)
                raise
            except Exception:
                log.exception("flusher loop crashed; sleeping before retry")
                await asyncio.sleep(1.0)

    async def _flush(self, batch: list[LogRecord]) -> None:
        assert self._client is not None
        payload = {"logs": [asdict(r) for r in batch]}
        for attempt in range(3):
            try:
                resp = await self._client.post(self.endpoint, json=payload)
                if resp.status_code < 400:
                    return
                log.warning("ingestion %s on attempt %d: %s",
                            resp.status_code, attempt + 1, resp.text[:200])
            except httpx.HTTPError as exc:
                log.warning("ingestion network error attempt %d: %s", attempt + 1, exc)
            await asyncio.sleep(0.2 * (2 ** attempt))
        log.error("dropped %d inference logs after retries", len(batch))


def now_ms() -> int:
    return int(time.time() * 1000)
