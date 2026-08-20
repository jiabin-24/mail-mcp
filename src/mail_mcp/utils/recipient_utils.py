from __future__ import annotations

from typing import Any


def recipient_address(recipient: Any) -> str:
    """提取单个收件人中的邮箱地址，兼容 dict、对象、字符串等输入。"""
    if recipient is None:
        return ""

    if isinstance(recipient, str):
        return recipient.strip()

    if isinstance(recipient, bytes):
        return recipient.decode("utf-8", errors="ignore").strip()

    if isinstance(recipient, dict):
        email_address = recipient.get("emailAddress", {})
        if isinstance(email_address, dict):
            return str(email_address.get("address", "") or "").strip()
        return str(recipient.get("address", "") or "").strip()

    nested_email_address = getattr(recipient, "emailAddress", None)
    if nested_email_address is not None:
        if isinstance(nested_email_address, dict):
            return str(nested_email_address.get("address", "") or "").strip()
        nested = getattr(nested_email_address, "address", None)
        if nested:
            return str(nested).strip()

    direct_address = getattr(recipient, "email_address", None) or getattr(recipient, "address", None) or ""
    return str(direct_address).strip()


def recipient_addresses(recipients: Any) -> list[str]:
    """提取收件人列表中的邮箱地址，自动处理 None/单值/可迭代输入。"""
    if not recipients:
        return []

    if isinstance(recipients, (str, bytes, dict)):
        source = [recipients]
    else:
        try:
            source = list(recipients)
        except TypeError:
            source = [recipients]

    result: list[str] = []
    for recipient in source:
        address = recipient_address(recipient)
        if address:
            result.append(address)
    return result