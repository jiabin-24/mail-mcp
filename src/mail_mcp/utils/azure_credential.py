from __future__ import annotations

import os

from azure.identity import ClientSecretCredential


def build_client_secret_credential_from_env() -> ClientSecretCredential:
    """基于 AZURE_* 环境变量构建应用身份凭据。"""
    tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
    client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("AZURE_CLIENT_SECRET") or "").strip()

    missing = [
        key
        for key, value in (
            ("AZURE_TENANT_ID", tenant_id),
            ("AZURE_CLIENT_ID", client_id),
            ("AZURE_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"缺少 Azure 应用身份环境变量: {', '.join(missing)}")

    return ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )


__all__ = ["build_client_secret_credential_from_env"]