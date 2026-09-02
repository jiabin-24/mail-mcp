import unittest
from unittest.mock import MagicMock, patch

from mail_mcp.stores.attachment_store import AttachmentStore


class AttachmentStoreTests(unittest.TestCase):
    @patch("mail_mcp.stores.attachment_store.httpx.Client")
    def test_lists_attachments_from_configured_host(self, mock_client_cls) -> None:
        response = MagicMock()
        response.status_code = 200
        response.content = b'[{"name":"report.pdf","link":"https://files/report.pdf"}]'
        response.json.return_value = [
            {"name": "report.pdf", "link": "https://files/report.pdf"}
        ]
        mock_client_cls.return_value.__enter__.return_value.get.return_value = response

        store = AttachmentStore("https://attachments.example.com/")
        result = store.list_message_attachments("draft/id")

        mock_client_cls.assert_called_once_with(
            base_url="https://attachments.example.com",
            timeout=30.0,
        )
        mock_client_cls.return_value.__enter__.return_value.get.assert_called_once_with(
            "/api/messages/draft%2Fid/attachments",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(
            result,
            [{"name": "report.pdf", "link": "https://files/report.pdf"}],
        )

    @patch("mail_mcp.stores.attachment_store.httpx.Client")
    def test_raises_readable_error_for_failed_request(self, mock_client_cls) -> None:
        response = MagicMock()
        response.status_code = 404
        response.json.return_value = {"error": "draft not found"}
        mock_client_cls.return_value.__enter__.return_value.get.return_value = response

        store = AttachmentStore("https://attachments.example.com")

        with self.assertRaisesRegex(
            ValueError,
            "Attachment service request failed \\(404\\)",
        ):
            store.list_message_attachments("missing-draft")


if __name__ == "__main__":
    unittest.main()