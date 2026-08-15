from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def require_not_none(value: T | None, *, name: str = "value") -> T:
    """Return a non-None value or raise a clear validation error."""
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def require_str(value: str | None, *, name: str = "value") -> str:
    """Return a non-empty text value or raise a clear validation error."""
    text = require_not_none(value, name=name)
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text
