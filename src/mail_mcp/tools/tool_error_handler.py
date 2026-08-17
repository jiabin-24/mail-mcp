import functools
import inspect
import logging

LOGGER = logging.getLogger("mail_mcp")


def tool_exception_logging(func):
    """Wrap tool execution so exceptions are printed with tracebacks and logged."""

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                LOGGER.exception(
                    "Tool %s failed: %s",
                    func.__name__,
                    exc,
                    exc_info=True,
                )
                raise

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            LOGGER.exception(
                "Tool %s failed: %s",
                func.__name__,
                exc,
                exc_info=True,
            )
            raise

    return sync_wrapper
