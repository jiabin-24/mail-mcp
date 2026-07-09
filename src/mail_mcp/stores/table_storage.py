from __future__ import annotations

import os
from dataclasses import dataclass

from azure.core.exceptions import ResourceExistsError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import ClientSecretCredential


@dataclass
class AzureTableContext:
    account_name: str
    table_name: str
    credential: ClientSecretCredential
    table_client: TableClient


def build_table_context_from_env(table_name: str, *, optional: bool = False) -> AzureTableContext | None:
    """Build Azure Table client context from AZURE_* environment variables."""

    account_name = (os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
    tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
    client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("AZURE_CLIENT_SECRET") or "").strip()

    missing = [
        key
        for key, value in (
            ("AZURE_STORAGE_ACCOUNT_NAME", account_name),
            ("AZURE_TENANT_ID", tenant_id),
            ("AZURE_CLIENT_ID", client_id),
            ("AZURE_CLIENT_SECRET", client_secret),
        )
        if not value
    ]

    if missing:
        if optional:
            return None
        raise ValueError(f"Missing Azure Table env vars: {', '.join(missing)}")

    account_url = f"https://{account_name}.table.core.windows.net"
    credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    service_client = TableServiceClient(endpoint=account_url, credential=credential)
    table_client = service_client.get_table_client(table_name=table_name)
    _ensure_table_exists(table_client)

    return AzureTableContext(
        account_name=account_name,
        table_name=table_name,
        credential=credential,
        table_client=table_client,
    )


def _ensure_table_exists(table_client: TableClient) -> None:
    try:
        table_client.create_table()
    except ResourceExistsError:
        return
