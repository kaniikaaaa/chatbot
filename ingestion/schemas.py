from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class LogIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern="^(success|error|cancelled)$")
    started_at: float
    completed_at: float | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    input_preview: str | None = Field(default=None, max_length=1200)
    output_preview: str | None = Field(default=None, max_length=1200)
    streamed: bool = False
    pii_redacted: bool = False
    error_message: str | None = Field(default=None, max_length=1200)
    conversation_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchIn(BaseModel):
    logs: list[LogIn] = Field(min_length=1, max_length=200)
