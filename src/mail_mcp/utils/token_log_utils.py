from __future__ import annotations

import logging
import os

TOKEN_LOG_MODE_ENV = "DELEGATED_TOKEN_LOG_MODE"
TOKEN_LOG_MODE_MASKED = "masked"
TOKEN_LOG_MODE_FULL = "full"
TOKEN_LOG_MODE_NONE = "none"
TOKEN_PREVIEW_LENGTH = 12


def resolve_token_log_mode() -> str:
    mode = os.getenv(TOKEN_LOG_MODE_ENV, TOKEN_LOG_MODE_NONE).strip().lower()
    if mode in {TOKEN_LOG_MODE_MASKED, TOKEN_LOG_MODE_FULL, TOKEN_LOG_MODE_NONE}:
        return mode

    return TOKEN_LOG_MODE_NONE


def masked_token(token: str) -> str:
    if len(token) > TOKEN_PREVIEW_LENGTH:
        return token[:TOKEN_PREVIEW_LENGTH] + "..."
    return token


def log_token_value(
    logger: logging.Logger,
    token: str,
    *,
    full_key: str,
    preview_key: str,
) -> None:
    token_log_mode = resolve_token_log_mode()
    if token_log_mode == TOKEN_LOG_MODE_FULL:
        logger.info("%s=%s", full_key, token)
    elif token_log_mode == TOKEN_LOG_MODE_MASKED:
        logger.info("%s=%s", preview_key, masked_token(token))