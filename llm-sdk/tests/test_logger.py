import asyncio

from llm_sdk.logger import InferenceLogger, LogRecord


class Response:
    status_code = 200
    text = ""


class Client:
    def __init__(self):
        self.calls = []

    async def post(self, endpoint, json, headers):
        self.calls.append((endpoint, json, headers))
        return Response()


def test_flush_uses_write_key(monkeypatch):
    monkeypatch.setenv("INGESTION_WRITE_KEY", "k")
    logger = InferenceLogger(endpoint="http://example.test")
    logger._client = Client()
    asyncio.run(logger._flush([LogRecord(request_id="r", provider="p", model="m", status="success", started_at=1.0)]))
    assert logger._client.calls[0][2] == {"X-Ingestion-Key": "k"}
