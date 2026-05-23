from main import _sse


def test_sse_frame():
    assert _sse({"type": "end"}) == b'data: {"type": "end"}\n\n'
