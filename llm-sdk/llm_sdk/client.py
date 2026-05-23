from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import AsyncIterator

import httpx

from .logger import InferenceLogger, LogRecord
from .pii import redact

PREVIEW_CHARS = 500


class LLMClient:
    def __init__(self, logger: InferenceLogger, redact_pii: bool = True) -> None:
        self.logger = logger
        self.redact_pii = redact_pii

    async def chat(
        self,
        *,
        provider: str,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        conversation_id: str,
        message_id: str,
        request_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        cancel_event: asyncio.Event | None = None,
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
        input_text = "\n".join(item.get("content", "") for item in messages)
        safe_input, input_hit = redact(input_text) if self.redact_pii else (input_text, False)
        record.input_preview = (safe_input or "")[:PREVIEW_CHARS]
        record.pii_redacted = input_hit

        if stream:
            return self._stream(provider, model, messages, temperature, max_tokens, record, cancel_event)
        return await self._once(provider, model, messages, temperature, max_tokens, record)

    async def _once(self, provider, model, messages, temperature, max_tokens, record):
        started = time.monotonic()
        try:
            text, usage = await PROVIDERS[provider].complete(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
            )
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.completed_at = time.time()
            record.prompt_tokens = usage.get("prompt_tokens")
            record.completion_tokens = usage.get("completion_tokens")
            record.total_tokens = usage.get("total_tokens")
            safe_output, output_hit = redact(text) if self.redact_pii else (text, False)
            record.output_preview = (safe_output or "")[:PREVIEW_CHARS]
            record.pii_redacted = record.pii_redacted or output_hit
            return text, record
        except Exception as exc:
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.completed_at = time.time()
            raise
        finally:
            self.logger.log(record)

    async def _stream(self, provider, model, messages, temperature, max_tokens, record, cancel_event):
        started = time.monotonic()
        first_token_at: float | None = None
        chunks: list[str] = []
        try:
            async for chunk in PROVIDERS[provider].stream(
                model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
            ):
                if cancel_event and cancel_event.is_set():
                    record.status = "cancelled"
                    break
                if first_token_at is None:
                    first_token_at = time.monotonic()
                    record.ttft_ms = int((first_token_at - started) * 1000)
                chunks.append(chunk)
                yield chunk
            if cancel_event and cancel_event.is_set():
                record.status = "cancelled"
            text = "".join(chunks)
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.completed_at = time.time()
            record.completion_tokens = max(1, len(text) // 4) if text else 0
            safe_output, output_hit = redact(text) if self.redact_pii else (text, False)
            record.output_preview = (safe_output or "")[:PREVIEW_CHARS]
            record.pii_redacted = record.pii_redacted or output_hit
        except Exception as exc:
            record.status = "error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            record.latency_ms = int((time.monotonic() - started) * 1000)
            record.completed_at = time.time()
            raise
        finally:
            self.logger.log(record)


class OpenAIProvider:
    def _client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            http_client=httpx.AsyncClient(timeout=30.0, trust_env=False),
        )

    async def complete(self, *, model, messages, temperature, max_tokens):
        response = await self._client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens
        )
        usage = response.usage.model_dump() if response.usage else {}
        return response.choices[0].message.content or "", usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        stream = await self._client().chat.completions.create(
            model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, stream=True
        )
        async for event in stream:
            delta = event.choices[0].delta.content if event.choices else None
            if delta:
                yield delta


class AnthropicProvider:
    def _client(self):
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def _split(self, messages):
        system = next((item["content"] for item in messages if item["role"] == "system"), "")
        rest = [item for item in messages if item["role"] != "system"]
        return system, rest

    async def complete(self, *, model, messages, temperature, max_tokens):
        system, rest = self._split(messages)
        response = await self._client().messages.create(
            model=model, messages=rest, system=system, temperature=temperature, max_tokens=max_tokens
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }
        return text, usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        system, rest = self._split(messages)
        async with self._client().messages.stream(
            model=model, messages=rest, system=system, temperature=temperature, max_tokens=max_tokens
        ) as stream:
            async for text in stream.text_stream:
                yield text


class GeminiProvider:
    def _client(self, model):
        import google.generativeai as genai

        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        return genai.GenerativeModel(model)

    def _convert(self, messages):
        system = " ".join(item["content"] for item in messages if item["role"] == "system")
        output = []
        for item in messages:
            if item["role"] == "system":
                continue
            role = "user" if item["role"] == "user" else "model"
            text = item["content"]
            if system and not output and role == "user":
                text = f"{system}\n\n{text}"
            output.append({"role": role, "parts": [text]})
        return output

    async def complete(self, *, model, messages, temperature, max_tokens):
        response = await self._client(model).generate_content_async(
            self._convert(messages),
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
        )
        usage = {}
        if getattr(response, "usage_metadata", None):
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count,
            }
        return response.text or "", usage

    async def stream(self, *, model, messages, temperature, max_tokens):
        response = await self._client(model).generate_content_async(
            self._convert(messages),
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
            stream=True,
        )
        async for chunk in response:
            if chunk.text:
                yield chunk.text


PROVIDERS = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
}
