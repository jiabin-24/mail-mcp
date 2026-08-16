import os
import unittest
from unittest.mock import patch

from mail_mcp.stores.table_storage import build_table_context_from_env


class BuildTableContextFromEnvTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_ACCOUNT_NAME": "demoacct",
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_CLIENT_ID": "client-id",
            "AZURE_CLIENT_SECRET": "client-secret",
        },
        clear=False,
    )
    @patch("mail_mcp.stores.table_storage.TableServiceClient")
    @patch("mail_mcp.stores.table_storage.ClientSecretCredential")
    def test_optional_returns_none_when_table_creation_is_unauthorized(
        self,
        mock_credential_cls,
        mock_service_client_cls,
    ):
        mock_table_client = mock_service_client_cls.return_value.get_table_client.return_value
        mock_table_client.create_table.side_effect = Exception(
            "This request is not authorized to perform this operation using this permission."
        )

        result = build_table_context_from_env("EmailSendQueue", optional=True)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
