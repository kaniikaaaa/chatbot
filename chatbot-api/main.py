from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "llm-sdk"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from db import close_pool, init_pool, pool
from llm_sdk import InferenceLogger, LLMClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("chatbot-api")

CONTEXT_TURNS = int(os.getenv("CONTEXT_TURNS", "12"))
DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "openai")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4o-mini")
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
ALLOWED_ORIGINS = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if item.strip()]

_logger: InferenceLogger | None = None
_client: LLMClient | None = None
_cancel_events: dict[str, asyncio.Event] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _logger, _client
    await init_pool()
    _logger = InferenceLogger()
    await _logger.start()
    _client = LLMClient(_logger, redact_pii=os.getenv("PII_REDACT", "1") == "1")
    yield
    await _logger.stop()
    await close_pool()


app = FastAPI(title="Chatbot API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class CreateConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=120)


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
    conversation_id: uuid.UUID
    content: str = Field(min_length=1, max_length=8000)
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    stream: bool = True
    temperature: float = Field(default=0.7, ge=0, le=2)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.url.path == "/healthz" or request.method == "OPTIONS":
        return await call_next(request)
    if not API_AUTH_TOKEN:
        return JSONResponse(status_code=503, content={"detail": "API auth is not configured"})
    if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {API_AUTH_TOKEN}"):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.post("/v1/conversations", response_model=ConversationOut)
async def create_conversation(body: CreateConversationIn):
    async with pool().acquire() as conn:
        row = await conn.fetchrow("INSERT INTO conversations (title) VALUES ($1) RETURNING *", body.title)
    return _conversation(row)


@app.get("/v1/conversations", response_model=list[ConversationOut])
async def list_conversations():
    async with pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 100")
    return [_conversation(row) for row in rows]


@app.get("/v1/conversations/{cid}", response_model=ConversationOut)
async def get_conversation(cid: uuid.UUID):
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", cid)
    if not row:
        raise HTTPException(404, "conversation not found")
    return _conversation(row)


@app.get("/v1/conversations/{cid}/messages", response_model=list[MessageOut])
async def list_messages(cid: uuid.UUID):
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id=$1 ORDER BY created_at",
            cid,
        )
    return [MessageOut(id=str(row["id"]), role=row["role"], content=row["content"], created_at=row["created_at"].isoformat()) for row in rows]


@app.post("/v1/conversations/{cid}/cancel")
async def cancel_conversation(cid: uuid.UUID):
    event = _cancel_events.get(str(cid))
    if event:
        event.set()
    async with pool().acquire() as conn:
        await conn.execute("UPDATE conversations SET status='cancelled', updated_at=NOW() WHERE id=$1", cid)
    return {"ok": True, "cancelled_inflight": event is not None}


@app.delete("/v1/conversations/{cid}")
async def delete_conversation(cid: uuid.UUID):
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM conversations WHERE id=$1", cid)
    return {"ok": True}


@app.post("/v1/chat")
async def chat(body: ChatIn):
    assert _client is not None
    async with pool().acquire() as conn:
        conversation = await conn.fetchrow("SELECT * FROM conversations WHERE id=$1", body.conversation_id)
        if not conversation:
            raise HTTPException(404, "conversation not found")
        if conversation["status"] == "cancelled":
            await conn.execute("UPDATE conversations SET status='active', updated_at=NOW() WHERE id=$1", body.conversation_id)
        user = await conn.fetchrow(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'user', $2) RETURNING id",
            body.conversation_id,
            body.content,
        )
        history_rows = await conn.fetch(
            "SELECT role, content FROM messages WHERE conversation_id=$1 ORDER BY created_at DESC LIMIT $2",
            body.conversation_id,
            CONTEXT_TURNS,
        )
    history = [{"role": row["role"], "content": row["content"]} for row in reversed(history_rows)]
    request_id = str(uuid.uuid4())
    if body.stream:
        return StreamingResponse(_stream(body, history, str(user["id"]), request_id), media_type="text/event-stream")
    try:
        text, _record = await _client.chat(
            provider=body.provider,
            model=body.model,
            messages=history,
            stream=False,
            conversation_id=str(body.conversation_id),
            message_id=str(user["id"]),
            request_id=request_id,
            temperature=body.temperature,
        )
    except Exception as exc:
        raise HTTPException(502, f"provider error: {exc}")
    assistant_id = await _persist_assistant(body.conversation_id, text)
    return {"request_id": request_id, "message_id": assistant_id, "content": text}


async def _stream(body: ChatIn, history: list[dict[str, str]], user_id: str, request_id: str) -> AsyncIterator[bytes]:
    assert _client is not None
    event = asyncio.Event()
    cid = str(body.conversation_id)
    _cancel_events[cid] = event
    chunks: list[str] = []
    try:
        yield _sse({"type": "start", "request_id": request_id})
        async for chunk in await _client.chat(
            provider=body.provider,
            model=body.model,
            messages=history,
            stream=True,
            conversation_id=cid,
            message_id=user_id,
            request_id=request_id,
            temperature=body.temperature,
            cancel_event=event,
        ):
            chunks.append(chunk)
            yield _sse({"type": "delta", "content": chunk})
        yield _sse({"type": "cancelled"} if event.is_set() else {"type": "end"})
    except Exception as exc:
        log.exception("stream failed")
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        _cancel_events.pop(cid, None)
        text = "".join(chunks)
        if text:
            assistant_id = await _persist_assistant(body.conversation_id, text)
            yield _sse({"type": "saved", "message_id": assistant_id})


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _persist_assistant(conversation_id: uuid.UUID, content: str) -> str:
    async with pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'assistant', $2) RETURNING id",
            conversation_id,
            content,
        )
        await conn.execute("UPDATE conversations SET updated_at=NOW() WHERE id=$1", conversation_id)
    return str(row["id"])


def _conversation(row) -> ConversationOut:
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
