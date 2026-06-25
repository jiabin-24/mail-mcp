from __future__ import annotations

import logging
import os


class ColorLevelFormatter(logging.Formatter):
    """Render logs as '<colored LEVEL>:    <message>' with four spaces."""

    RESET = "\x1b[0m"
    COLORS = {
        logging.DEBUG: "\x1b[36m",
        logging.INFO: "\x1b[32m",
        logging.WARNING: "\x1b[33m",
        logging.ERROR: "\x1b[31m",
        logging.CRITICAL: "\x1b[1;31m",
    }

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        use_color = os.getenv("NO_COLOR") is None
        if use_color:
            color = self.COLORS.get(record.levelno, "")
            prefix = f"{color}{record.levelname}:{self.RESET}" if color else f"{record.levelname}:"
        else:
            prefix = f"{record.levelname}:"

        return f"{prefix}    {message}"


def configure_namespace_logger(namespace: str, handler_name: str) -> None:
    """Ensure logs in a namespace are consistently prefixed with level name."""
    logger = logging.getLogger(namespace)
    logger.setLevel(logging.INFO)

    for existing in logger.handlers:
        if existing.get_name() == handler_name:
            return

    handler = logging.StreamHandler()
    handler.set_name(handler_name)
    handler.setFormatter(ColorLevelFormatter())
    logger.addHandler(handler)
    logger.propagate = False


def configure_default_loggers() -> None:
    """Configure default logger namespaces used by this service."""
    configure_namespace_logger("mcp", "mail_mcp_mcp_stream_handler")
    configure_namespace_logger("mail_mcp", "mail_mcp_namespace_stream_handler")
