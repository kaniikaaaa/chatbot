"""Provider-agnostic chat client. Each provider's SDK is imported lazily
so the package doesn't fail to import when only one is configured.

Streaming yields plain text chunks; the SDK fills in token counts and
latency once the stream closes."""
from __future__ import annotations

import os
import time
import uuid
from typing import AsyncIterator

from .logger import InferenceLogger, LogRecord, now_ms
from .pii import redact


PREVIEW_CHARS = 500


class LLMClient:
    def __init__(self, logger: InferenceLogger, redact_pii: bool = True) -> None:
        self._logger = logger
        self._redact = redact_pii

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        conversation_id: str | None = None,
        message_id: str | None = None,
        request_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ):
        rid = request_id or str(uuid.uuid4())
        record = LogRecord(
            request_id=rid,
            provider=provider,
            model=model,
            status="success",
            started_at=time.time(),
            conversation_id=conversation_id,
            message_id=message_id,
            streamed=stream,
            metadata={"temperature": temperature, "max_tokens": max_tokens},
        )

        prompt_text = "\n".join(m.get("content", "") for m in messages)
        redacted_prompt, prompt_hit = (redact(prompt_text) if self._redact else (prompt_text, False))
        record.input_preview = (redacted_prompt or "")[:PREVIEW_CHARS]
        record.pii_redacted = prompt_hit

        if stream:
            return self._chat_stream(provider, model, messages, temperature, max_tokens, record)
        return await self._chat_once(provider, model, messages, temperature, max_tokens, record)

    async def _chat_once(self, provider, model, messages, temperature, max_tokens, record):
        t0 = time.monotonic()
        try:
            text, usage = await _PROVIDERS[provider].complete(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            )
            record.latency_ms = int((time.monotonic() - t0) * 1000)
            record.completed_at = time.time()
            record.prompt_tokens = usage.get("prompt_tokens")
            record.completion_tokens = usage.get("completion_tokens")
            record.total_tokens = usage.get("total_tokens")
            redacted_out, out_hit = (redact(text) if self._redact else (text, False))
            record.output_preview = (redacted_out or "")[:PREVIEW_CHARS]
            record.pii_redacted = record.pii_redacted or out_hit
            return text, record
        except Exception as exc:
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            record.latency_ms = int((time.monotonic() - t0) * 1000)
            record.completed_at = time.time()
            raise
        finally:
            self._logger.log(record)

    async def _chat_stream(self, provider, model, messages, temperature, max_tokens, record) -> AsyncIterator[str]:
        t0 = time.monotonic()
        first_token_at: float | None = None
        chunks: list[str] = []
        try:
            async for chunk in _PROVIDERS[provider].stream(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
            ):
                if first_token_at is None:
                    first_token_at = time.monotonic()
                    record.ttft_ms = int((first_token_at - t0) * 1000)
                chunks.append(chunk)
                yield chunk
            record.latency_ms = int((time.monotonic() - t0) * 1000)
            record.completed_at = time.time()
            text = "".join(chunks)
            # Best-effort token count using a single approximation
            # (avoids per-provider tokenizer drift in the hot path).
            record.completion_tokens = max(1, len(text) // 4) if text else 0
            redacted_out, out_hit = (redact(text) if self._redact else (text, False))
            record.output_preview = (redacted_out or "")[:PREVIEW_CHARS]
            record.pii_redacted = record.pii_redacted or out_hit
        except Exception as exc:
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            record.latency_ms = int((time.monotonic() - t0) * 1000)
            record.completed_at = time.time()
            raise
        finally:
            self._logger.log(record)


# --- Provider adapters --------------------------------------------------------

class _OpenAI:
    name = "openai"

    def _client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def complete(self, *, model, messages, temperature, max_tokens):
        client = self._client()
        resp = await client.chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        )
        usage = resp.usage.model_dump() if resp.usage else {}
        return resp.choices[0].message.content or "", usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        client = self._client()
        stream = await client.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, stream=True,
        )
        async for event in stream:
            delta = event.choices[0].delta.content if event.choices else None
            if delta:
                yield delta


class _Anthropic:
    name = "anthropic"

    def _client(self):
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def _split(self, messages):
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        rest = [m for m in messages if m["role"] != "system"]
        return system, rest

    async def complete(self, *, model, messages, temperature, max_tokens):
        client = self._client()
        system, rest = self._split(messages)
        resp = await client.messages.create(
            model=model, messages=rest, system=system or "",
            temperature=temperature, max_tokens=max_tokens,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = {
            "prompt_tokens": resp.usage.input_tokens,
            "completion_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.input_tokens + resp.usage.output_tokens,
        }
        return text, usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        client = self._client()
        system, rest = self._split(messages)
        async with client.messages.stream(
            model=model, messages=rest, system=system or "",
            temperature=temperature, max_tokens=max_tokens,
        ) as s:
            async for text in s.text_stream:
                yield text


class _Gemini:
    name = "gemini"

    def _client(self, model):
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        return genai.GenerativeModel(model)

    def _convert(self, messages):
        # Gemini does not have a system role; flatten it into the first user turn.
        sys_text = " ".join(m["content"] for m in messages if m["role"] == "system")
        out = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "user" if m["role"] == "user" else "model"
            text = m["content"]
            if sys_text and role == "user" and not out:
                text = f"{sys_text}\n\n{text}"
            out.append({"role": role, "parts": [text]})
        return out

    async def complete(self, *, model, messages, temperature, max_tokens):
        m = self._client(model)
        resp = await m.generate_content_async(
            self._convert(messages),
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
        )
        text = resp.text or ""
        usage = {}
        try:
            usage = {
                "prompt_tokens": resp.usage_metadata.prompt_token_count,
                "completion_tokens": resp.usage_metadata.candidates_token_count,
                "total_tokens": resp.usage_metadata.total_token_count,
            }
        except AttributeError:
            pass
        return text, usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        m = self._client(model)
        resp = await m.generate_content_async(
            self._convert(messages),
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
            stream=True,
        )
        async for chunk in resp:
            if chunk.text:
                yield chunk.text


_PROVIDERS = {p.name: p() for p in (_OpenAI, _Anthropic, _Gemini)}
