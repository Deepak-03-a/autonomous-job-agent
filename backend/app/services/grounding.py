"""Checks that generated text only uses facts the app actually has."""

import re

MIN_EVIDENCE_CHARS = 6


def flatten_strings(value):
    """Every string (and number rendered as text) inside a nested structure."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from flatten_strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from flatten_strings(item)
    elif value is not None:
        yield str(value)


def haystack(context) -> str:
    return "\n".join(flatten_strings(context)).casefold()


def evidence_grounded(evidence, context) -> bool:
    """Every evidence item must be a real quote (6+ characters) from the context."""
    if not isinstance(evidence, list) or not evidence:
        return False
    text = haystack(context)
    return all(isinstance(item, str) and len(item.strip()) >= MIN_EVIDENCE_CHARS
               and item.strip().casefold() in text for item in evidence)


def numbers_grounded(answer: str, context) -> bool:
    """Every number in the answer must appear in the context, so statistics cannot
    be invented. A rounded figure fails on purpose: the caller falls back to the
    deterministic text."""
    text = haystack(context)
    return all(number in text for number in re.findall(r"\d+(?:\.\d+)?", answer or ""))
