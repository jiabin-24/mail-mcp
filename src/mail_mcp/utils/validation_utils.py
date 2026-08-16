from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def require_not_none(value: T | None, *, name: str = "value") -> T:
    """返回非空值；若为空则抛出清晰的校验错误。"""
    if value is None:
        raise ValueError(f"{name} is required")
    return value


def require_str(value: str | None, *, name: str = "value") -> str:
    """返回非空字符串；若为空则抛出清晰的校验错误。"""
    text = require_not_none(value, name=name)
    if not text:
        raise ValueError(f"{name} cannot be empty")
    return text
