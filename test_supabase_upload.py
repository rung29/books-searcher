import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import Mock, patch

import supabase_upload


class SupabaseUploadTests(unittest.TestCase):
    def test_loads_local_env_without_overwriting_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env.local"
            env_file.write_text(
                "SUPABASE_URL=https://file.supabase.co\n"
                "SUPABASE_BUCKET=book-data\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"SUPABASE_URL": "https://existing.supabase.co"},
                clear=True,
            ):
                supabase_upload.load_env_file(env_file)
                self.assertEqual(
                    os.environ["SUPABASE_URL"],
                    "https://existing.supabase.co",
                )
                self.assertEqual(os.environ["SUPABASE_BUCKET"], "book-data")

    def test_new_secret_key_is_only_sent_as_api_key(self):
        headers = supabase_upload._storage_headers("sb_secret_example")

        self.assertEqual(headers["apikey"], "sb_secret_example")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["x-upsert"], "true")

    def test_legacy_service_role_key_uses_bearer_header(self):
        headers = supabase_upload._storage_headers("eyJlegacy")

        self.assertEqual(headers["Authorization"], "Bearer eyJlegacy")

    @patch("supabase_upload.requests.post")
    def test_uploads_json_to_expected_storage_path(self, mock_post):
        mock_post.return_value = Mock(ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ebooks_data.json"
            path.write_text("[]", encoding="utf-8")

            supabase_upload.upload_file(
                "https://project.supabase.co",
                "sb_secret_example",
                "book-data",
                "latest/ebooks_data.json",
                path,
            )

        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            "https://project.supabase.co/storage/v1/object/"
            "book-data/latest/ebooks_data.json",
        )
        self.assertEqual(kwargs["data"], b"[]")

    def test_writes_public_config_without_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            base_url = supabase_upload.write_public_config(
                Path(tmp),
                "https://project.supabase.co",
                "book-data",
                "latest",
            )
            config = (Path(tmp) / "data_source_config.js").read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            base_url,
            "https://project.supabase.co/storage/v1/object/public/"
            "book-data/latest",
        )
        self.assertIn(base_url, config)
        self.assertNotIn("secret", config.lower().replace("secret key", ""))


if __name__ == "__main__":
    unittest.main()
