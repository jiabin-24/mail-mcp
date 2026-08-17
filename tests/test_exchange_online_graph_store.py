from mail_mcp.stores.exchange_online.email_store import EmailStore
from mail_mcp.stores.exchange_online.graph_gateway import GraphGateway
from mail_mcp.stores.gateway_base import GatewayBase


def test_exchange_online_graph_store_is_the_graph_implementation() -> None:
    assert issubclass(EmailStore, GraphGateway)
    assert issubclass(GraphGateway, GatewayBase)
