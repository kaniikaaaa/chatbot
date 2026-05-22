"""Regex-based PII redaction. Kept intentionally narrow — runs in the
hot path of every inference call, so we trade depth for speed."""
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[API_KEY]"),
]


def redact(text: str | None) -> tuple[str | None, bool]:
    if not text:
        return text, False
    out = text
    hit = False
    for pat, repl in _PATTERNS:
        new = pat.sub(repl, out)
        if new != out:
            hit = True
            out = new
    return out, hit
