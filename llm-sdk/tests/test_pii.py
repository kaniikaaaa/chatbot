from llm_sdk.pii import redact


def test_redacts_common_pii():
    out, hit = redact("a@test.com 123-45-6789 sk-aaaaaaaaaaaaaaaaaaaaaaaa")
    assert hit
    assert "[EMAIL]" in out
    assert "[SSN]" in out
    assert "[API_KEY]" in out
