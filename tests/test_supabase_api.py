import json
import os
import subprocess
import types
import unittest
from io import BytesIO
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas_planner.supabase_api import SupabaseConfig


class SupabaseApiTests(unittest.TestCase):
    def setUp(self):
        self.config = SupabaseConfig(
            "https://example.supabase.co", "sb_publishable_test"
        )

    def test_module_imports_without_python_dotenv(self):
        script = """
import builtins
real_import = builtins.__import__
def import_without_dotenv(name, *args, **kwargs):
    if name == 'dotenv':
        raise ImportError('simulated missing browser dependency')
    return real_import(name, *args, **kwargs)
builtins.__import__ = import_without_dotenv
from atlas_planner.supabase_api import SupabaseConfig
assert SupabaseConfig
"""
        env = os.environ.copy()
        source_path = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (source_path, env.get("PYTHONPATH")))
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @patch("atlas_planner.supabase_api.urlopen")
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

    @patch("atlas_planner.supabase_api.urlopen")
    def test_profile_save_upserts_with_user_token(self, mocked_urlopen):
        saved = {"user_id": "user-1", "username": "atlas_user", "common_parcels": 12}
        response = BytesIO(json.dumps([saved]).encode())
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        profile = {"user_id": "user-1", "username": "atlas_user", "common_parcels": 12}

        result = self.config.save_profile(profile, "user-access-token")

        self.assertEqual(result, saved)
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Prefer"), "resolution=merge-duplicates,return=representation")
        self.assertEqual(json.loads(request.data), profile)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("atlas_planner.supabase_api.urlopen")
    def test_refresh_session_uses_refresh_grant(self, mocked_urlopen):
        response = BytesIO(b'{"access_token":"new-access","refresh_token":"rotated-refresh"}')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        result = self.config.refresh_session("old-refresh")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("grant_type=refresh_token", request.full_url)
        self.assertEqual(json.loads(request.data), {"refresh_token": "old-refresh"})
        self.assertEqual(result["refresh_token"], "rotated-refresh")

    @patch("atlas_planner.supabase_api.sys.platform", "emscripten")
    def test_browser_auth_uses_fetch_with_javascript_options(self):
        calls = []

        class Response:
            status = 200

            async def text(self):
                return '{"access_token":"access","user":{"id":"user-1"}}'

        async def fetch(url, options):
            calls.append((url, options))
            return Response()

        js_module = types.ModuleType("js")
        js_module.fetch = fetch
        pyodide_module = types.ModuleType("pyodide")
        pyodide_module.__path__ = []
        ffi_module = types.ModuleType("pyodide.ffi")
        ffi_module.to_js = lambda value: value

        with patch.dict(
            sys.modules,
            {"js": js_module, "pyodide": pyodide_module, "pyodide.ffi": ffi_module},
        ):
            result = self.config.sign_in("user@example.com", "password")

        self.assertEqual(result["access_token"], "access")
        url, options = calls[0]
        self.assertIn("grant_type=password", url)
        self.assertIn("apikey=sb_publishable_test", url)
        self.assertEqual(options["method"], "POST")
        self.assertEqual(json.loads(options["body"]), {
            "email": "user@example.com", "password": "password"
        })

    @patch("atlas_planner.supabase_api.urlopen")
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

        config.sign_up("user@example.com", "password", "atlas_user")

        request = mocked_urlopen.call_args.args[0]
        redirect = parse_qs(urlparse(request.full_url).query)["redirect_to"]
        self.assertEqual(redirect, ["http://127.0.0.1:8551/"])
        self.assertEqual(json.loads(request.data)["data"], {"username": "atlas_user"})

    @patch("atlas_planner.supabase_api.urlopen")
    def test_training_read_returns_only_anonymous_observation_fields(self, mocked_urlopen):
        response = BytesIO(b'[{"position":25,"victories":90,"event":{"game":"Racer"}}]')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        rows = self.config.get_training_records("user-access-token")

        self.assertEqual(rows[0]["position"], 25)
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("minigame_observations?select=", request.full_url)
        self.assertNotIn("user_id", request.full_url)

    @patch("atlas_planner.supabase_api.urlopen")
    def test_game_history_is_filtered_to_the_authenticated_user(self, mocked_urlopen):
        response = BytesIO(b'[{"game":"Racer","victories":20}]')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        history = self.config.get_game_sessions("user-1", "user-access-token")

        self.assertEqual(history, [{"game": "Racer", "victories": 20}])
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("user_id=eq.user-1", request.full_url)
        self.assertIn("review_status=neq.rejected", request.full_url)
        self.assertIn("order=event_date.desc,created_at.desc", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("atlas_planner.supabase_api.urlopen")
    def test_game_result_uses_unique_event_upsert(self, mocked_urlopen):
        response = BytesIO(b"")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        result = {
            "user_id": "user-1",
            "game": "Racer",
            "event_date": "2026-09-21",
            "victories": 42,
            "final_position": 25,
            "losses": 2,
            "activity_phase": "medio",
        }

        self.config.save_game_session(result, "user-access-token")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("on_conflict=user_id,game,event_date", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")
        self.assertEqual(json.loads(request.data), result)

    @patch("atlas_planner.supabase_api.urlopen")
    def test_game_session_exists_checks_the_user_and_event_key(self, mocked_urlopen):
        response = BytesIO(b'[{"id":7}]')
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        exists = self.config.game_session_exists(
            "user-1", "Racer", "2026-09-21", "user-access-token"
        )

        self.assertTrue(exists)
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("user_id=eq.user-1", request.full_url)
        self.assertIn("game=eq.Racer", request.full_url)
        self.assertIn("event_date=eq.2026-09-21", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("atlas_planner.supabase_api.urlopen")
    def test_admin_event_data_uses_dedicated_rpc_and_sends_brackets(self, mocked_urlopen):
        response = BytesIO(b"42")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response
        event = {
            "game": "Racer", "event_date": "2026-09-21", "duration_minutes": 720,
            "total_coins": 1000,
            "tiers": [
                {"position_min": 1, "position_max": 1, "coins_awarded": 20},
                {"position_min": 2, "position_max": 2, "coins_awarded": 15},
                {"position_min": 3, "position_max": 3, "coins_awarded": 10},
                {"position_min": 4, "position_max": 25, "coins_awarded": 100},
            ],
        }

        self.config.save_admin_event_data(event, "user-access-token")

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("admin_save_event_data", request.full_url)
        self.assertEqual(json.loads(request.data)["p_tiers"], event["tiers"])
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")

    @patch("atlas_planner.supabase_api.urlopen")
    def test_admin_event_lookup_is_by_date_and_returns_existing_game(self, mocked_urlopen):
        event_response = BytesIO(b'[{"id":42,"game":"Warship","total_coins":500,"event_format":"regular_tournament","archived":false}]')
        event_response.__enter__ = lambda obj: obj
        event_response.__exit__ = lambda *args: None
        tiers_response = BytesIO(b'[]')
        tiers_response.__enter__ = lambda obj: obj
        tiers_response.__exit__ = lambda *args: None
        mocked_urlopen.side_effect = [event_response, tiers_response]

        event = self.config.get_admin_event_data("2026-04-05", "user-access-token")

        self.assertEqual(event["game"], "Warship")
        request = mocked_urlopen.call_args_list[0].args[0]
        self.assertIn("event_date=eq.2026-04-05", request.full_url)
        self.assertIn("event_format=eq.regular_tournament", request.full_url)
        self.assertNotIn("game=eq.", request.full_url)

    @patch("atlas_planner.supabase_api.urlopen")
    def test_admin_observation_uses_dedicated_rpc(self, mocked_urlopen):
        response = BytesIO(b"null")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        self.config.add_admin_observation(
            "Racer", "2026-09-21",
            {"position": 25, "victories": 100, "losses": None, "activity_phase": None, "coins_earned": None},
            "user-access-token",
        )

        request = mocked_urlopen.call_args.args[0]
        self.assertIn("admin_add_observation", request.full_url)
        payload = json.loads(request.data)
        self.assertEqual(payload["p_event_date"], "2026-09-21")
        self.assertEqual(set(payload), {"p_game", "p_event_date", "p_position", "p_victories"})


if __name__ == "__main__":
    unittest.main()
