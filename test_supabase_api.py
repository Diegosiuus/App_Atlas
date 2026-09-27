import json
import unittest
from io import BytesIO
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from supabase_api import SupabaseConfig


class SupabaseApiTests(unittest.TestCase):
    def setUp(self):
        self.config = SupabaseConfig(
            "https://example.supabase.co", "sb_publishable_test"
        )

    @patch("supabase_api.urlopen")
    def test_profile_read_uses_user_token_and_rls_filter(self, mocked_urlopen):
        response = BytesIO(b'[{"user_id":"user-1"}]')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        profile = self.config.get_profile("user-1", "user-access-token")

        self.assertEqual(profile, {"user_id": "user-1"})
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("user_id=eq.user-1", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("supabase_api.urlopen")
    def test_profile_save_upserts_with_user_token(self, mocked_urlopen):
        response = BytesIO(b"")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        profile = {"user_id": "user-1", "common_parcels": 12}

        self.config.save_profile(profile, "user-access-token")

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Prefer"), "resolution=merge-duplicates,return=minimal")
        self.assertEqual(json.loads(request.data), profile)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("supabase_api.urlopen")
    def test_signup_sends_explicit_email_redirect(self, mocked_urlopen):
        response = BytesIO(b'{"user":{"id":"user-1"}}')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        config = SupabaseConfig(
            "https://example.supabase.co",
            "sb_publishable_test",
            "http://127.0.0.1:8551/",
        )

        config.sign_up("user@example.com", "password")

        request = mocked_urlopen.call_args.args[0]
        redirect = parse_qs(urlparse(request.full_url).query)["redirect_to"]
        self.assertEqual(redirect, ["http://127.0.0.1:8551/"])

    @patch("supabase_api.urlopen")
    def test_game_history_is_filtered_to_the_authenticated_user(self, mocked_urlopen):
        response = BytesIO(b'[{"game":"Racer","victories":20}]')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        history = self.config.get_game_sessions("user-1", "user-access-token")

        self.assertEqual(history, [{"game": "Racer", "victories": 20}])
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("user_id=eq.user-1", request.full_url)
        self.assertIn("order=event_date.desc,created_at.desc", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("supabase_api.urlopen")
    def test_game_result_uses_unique_event_upsert(self, mocked_urlopen):
        response = BytesIO(b"")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        result = {
            "user_id": "user-1",
            "game": "Racer",
            "event_date": "2026-09-20",
            "victories": 42,
            "final_position": 25,
            "played_minutes": 60,
        }

        self.config.save_game_session(result, "user-access-token")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("on_conflict=user_id,game,event_date", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")
        self.assertEqual(json.loads(request.data), result)


if __name__ == "__main__":
    unittest.main()
