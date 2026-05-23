from __future__ import annotations

import asyncio
import logging
import os
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
    def __init__(self, endpoint: str | None = None, max_queue: int = 10_000) -> None:
        self.endpoint = endpoint or os.getenv("INGESTION_URL", "http://ingestion:8001/v1/logs")
        self.write_key = os.getenv("INGESTION_WRITE_KEY", "")
        self._queue: asyncio.Queue[LogRecord] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._client = httpx.AsyncClient(timeout=5.0, trust_env=False)
            self._task = asyncio.create_task(self._run(), name="inference-log-flusher")

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
            log.warning("dropping inference log because queue is full: %s", record.request_id)

    async def _run(self) -> None:
        assert self._client is not None
        while True:
            try:
                batch = [await self._queue.get()]
                while not self._queue.empty() and len(batch) < 100:
                    batch.append(self._queue.get_nowait())
                await self._flush(batch)
            except asyncio.CancelledError:
                rest: list[LogRecord] = []
                while not self._queue.empty():
                    rest.append(self._queue.get_nowait())
                if rest:
                    await self._flush(rest)
                raise
            except Exception:
                log.exception("log flusher crashed")
                await asyncio.sleep(1)

    async def _flush(self, batch: list[LogRecord]) -> None:
        assert self._client is not None
        headers = {"X-Ingestion-Key": self.write_key} if self.write_key else {}
        payload = {"logs": [asdict(item) for item in batch]}
        for attempt in range(3):
            try:
                response = await self._client.post(self.endpoint, json=payload, headers=headers)
                if response.status_code < 400:
                    return
                log.warning("ingestion returned %s: %s", response.status_code, response.text[:200])
            except httpx.HTTPError as exc:
                log.warning("ingestion network error on attempt %d: %s", attempt + 1, exc)
            await asyncio.sleep(0.2 * (2**attempt))
        log.error("dropped %d inference logs after retries", len(batch))
