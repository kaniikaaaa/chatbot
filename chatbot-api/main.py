"""Chatbot API.

Multi-turn conversations backed by Postgres; LLM calls go through the
local llm-sdk so every inference is logged. Streaming uses SSE. A
per-conversation asyncio.Event lets the UI cancel an in-flight stream."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

# Make the in-repo SDK importable when running outside Docker.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm-sdk"))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db import init_pool, pool, close_pool
from llm_sdk import LLMClient, InferenceLogger

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chatbot-api")

CONTEXT_WINDOW = int(os.getenv("CONTEXT_TURNS", "12"))
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "openai")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4o-mini")

_cancel_events: dict[str, asyncio.Event] = {}
_logger: InferenceLogger | None = None
_client: LLMClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _logger, _client
    await init_pool()
    _logger = InferenceLogger()
    await _logger.start()
    _client = LLMClient(_logger, redact_pii=os.getenv("PII_REDACT", "1") == "1")
    log.info("chatbot-api ready")
    yield
    await _logger.stop()
    await close_pool()


app = FastAPI(title="Chatbot API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateConversationIn(BaseModel):
    title: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str | None
    status: str
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class ChatIn(BaseModel):
    conversation_id: str
    content: str = Field(min_length=1)
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    stream: bool = True
    temperature: float = 0.7


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

@app.post("/v1/conversations", response_model=ConversationOut)
async def create_conversation(body: CreateConversationIn):
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO conversations (title) VALUES ($1) RETURNING *",
            body.title,
        )
    return _conv_row(row)


@app.get("/v1/conversations", response_model=list[ConversationOut])
async def list_conversations():
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 100"
        )
    return [_conv_row(r) for r in rows]


@app.get("/v1/conversations/{cid}", response_model=ConversationOut)
async def get_conversation(cid: str):
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", uuid.UUID(cid))
    if not row:
        raise HTTPException(404, "conversation not found")
    return _conv_row(row)


@app.get("/v1/conversations/{cid}/messages", response_model=list[MessageOut])
async def list_messages(cid: str):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id = $1 ORDER BY created_at",
            uuid.UUID(cid),
        )
    return [
        MessageOut(id=str(r["id"]), role=r["role"], content=r["content"],
                   created_at=r["created_at"].isoformat())
        for r in rows
    ]


@app.post("/v1/conversations/{cid}/cancel")
async def cancel_conversation(cid: str):
    ev = _cancel_events.get(cid)
    if ev:
        ev.set()
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE conversations SET status='cancelled', updated_at=NOW() WHERE id=$1",
            uuid.UUID(cid),
        )
    return {"ok": True, "cancelled_inflight": ev is not None}


@app.delete("/v1/conversations/{cid}")
async def delete_conversation(cid: str):
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE id=$1", uuid.UUID(cid))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Chat (streaming + non-streaming)
# ---------------------------------------------------------------------------

@app.post("/v1/chat")
async def chat(body: ChatIn):
    assert _client is not None
    cid = body.conversation_id

    async with pool().acquire() as conn:
        conv = await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", uuid.UUID(cid))
        if not conv:
            raise HTTPException(404, "conversation not found")
        if conv["status"] == "cancelled":
            # Re-opening a cancelled conversation flips it back to active.
            await conn.execute(
                "UPDATE conversations SET status='active' WHERE id=$1", uuid.UUID(cid)
            )

        user_msg = await conn.fetchrow(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES ($1, 'user', $2) RETURNING id, role, content, created_at",
            uuid.UUID(cid), body.content,
        )

        history_rows = await conn.fetch(
            "SELECT role, content FROM messages WHERE conversation_id=$1 "
            "ORDER BY created_at DESC LIMIT $2",
            uuid.UUID(cid), CONTEXT_WINDOW,
        )

    history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)]
    request_id = str(uuid.uuid4())

    if body.stream:
        return StreamingResponse(
            _stream(cid, history, body, str(user_msg["id"]), request_id),
            media_type="text/event-stream",
        )

    text, _record = await _client.chat(
        provider=body.provider, model=body.model, messages=history,
        stream=False, conversation_id=cid, message_id=str(user_msg["id"]),
        request_id=request_id, temperature=body.temperature,
    )
    asst_id = await _persist_assistant(cid, text)
    return {"request_id": request_id, "message_id": asst_id, "content": text}


async def _stream(cid: str, history, body: ChatIn, user_msg_id: str, request_id: str) -> AsyncIterator[bytes]:
    assert _client is not None
    cancel = asyncio.Event()
    _cancel_events[cid] = cancel
    collected: list[str] = []
    try:
        yield _sse({"type": "start", "request_id": request_id})
        async for chunk in await _client.chat(
            provider=body.provider, model=body.model, messages=history,
            stream=True, conversation_id=cid, message_id=user_msg_id,
            request_id=request_id, temperature=body.temperature,
        ):
            if cancel.is_set():
                yield _sse({"type": "cancelled"})
                break
            collected.append(chunk)
            yield _sse({"type": "delta", "content": chunk})
        if not cancel.is_set():
            yield _sse({"type": "end"})
    except Exception as exc:
        log.exception("stream failed")
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        _cancel_events.pop(cid, None)
        text = "".join(collected)
        if text:
            asst_id = await _persist_assistant(cid, text)
            yield _sse({"type": "saved", "message_id": asst_id})


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _persist_assistant(cid: str, content: str) -> str:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES ($1, 'assistant', $2) RETURNING id",
            uuid.UUID(cid), content,
        )
        await conn.execute(
            "UPDATE conversations SET updated_at=NOW() WHERE id=$1", uuid.UUID(cid)
        )
    return str(row["id"])


def _conv_row(row) -> ConversationOut:
    return ConversationOut(
        id=str(row["id"]),
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}
