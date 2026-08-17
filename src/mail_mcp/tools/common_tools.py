from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from ..stores.graph_store import GraphStoreBase
from ..tools.tool_error_handler import tool_exception_logging
from ..utils.datetime_utils import resolve_zone_info


def register_common_tools(app: FastMCP, graph_store: GraphStoreBase) -> None:
    @app.tool()
    @tool_exception_logging
    def get_current_time() -> dict[str, str]:
        """Get the current time in the mailbox timezone, or fall back to UTC if unavailable."""
        zone_name = graph_store.get_mailbox_time_zone_if_available() or "UTC"
        tzinfo = resolve_zone_info(zone_name) or timezone.utc
        now_local = datetime.now(tzinfo)

        return {
            "timezone": zone_name,
            "iso_8601": now_local.isoformat(),
            "datetime": now_local.strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
