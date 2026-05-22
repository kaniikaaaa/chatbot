from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LogIn(BaseModel):
    request_id: str
    provider: str
    model: str
    status: str = Field(pattern="^(success|error|cancelled)$")
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
    metadata: dict[str, Any] = {}


class BatchIn(BaseModel):
    logs: list[LogIn]
