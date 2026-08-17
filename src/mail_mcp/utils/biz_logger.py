from __future__ import annotations

import logging
import os


class ColorLevelFormatter(logging.Formatter):
    """将日志格式化为 '<colored LEVEL>:    <message>' 的固定四空格样式。"""

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

        return f"{prefix}     {message}"


def configure_namespace_logger(namespace: str, handler_name: str) -> None:
    """确保同一命名空间下的日志都带有统一的级别前缀。"""
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
    """配置本服务默认使用的日志命名空间。"""
    configure_namespace_logger("mcp", "mail_mcp_mcp_stream_handler")
    configure_namespace_logger("mail_mcp", "mail_mcp_namespace_stream_handler")

    # Azure SDK 的 blob / pipeline 日志通常很嘈杂；默认仅展示 warning 及以上，避免控制台被淹没。
    for noisy_logger_name in (
        "azure",
        "azure.core",
        "azure.storage",
        "azure.storage.blob",
        "azure.core.pipeline",
        "azure.core.pipeline.policies",
        "urllib3",
    ):
        logger = logging.getLogger(noisy_logger_name)
        logger.setLevel(logging.WARNING)
        logger.propagate = False
