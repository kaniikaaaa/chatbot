import pytest
from pydantic import ValidationError

from schemas import BatchIn


def test_valid_batch():
    batch = BatchIn(logs=[{"request_id": "r1", "provider": "openai", "model": "m", "status": "success", "started_at": 1.0}])
    assert batch.logs[0].request_id == "r1"


def test_invalid_status():
    with pytest.raises(ValidationError):
        BatchIn(logs=[{"request_id": "r1", "provider": "openai", "model": "m", "status": "bad", "started_at": 1.0}])
