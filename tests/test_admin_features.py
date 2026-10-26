import sys
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas_planner.app import (
    _admin_import_rows,
    _build_admin_event_payload,
    _build_admin_observation_payload,
    _picker_selected_date,
    _build_profile_payload,
    _load_local_records,
    _local_registry_path,
    _training_records,
)
from atlas_planner.supabase_api import SupabaseConfig


class AdminImportTests(unittest.TestCase):
    def test_picker_prefers_selected_iso_date_from_event_over_stale_control_value(self):
        selected = _picker_selected_date("2026-10-26", date(2026, 10, 25))

        self.assertEqual(selected, date(2026, 10, 26))

    def test_admin_event_payload_contains_pool_and_all_aggregate_tiers(self):
        payload = _build_admin_event_payload(
            "Racer",
            date(2026, 9, 21),
            "1000",
            {1: "20", 2: "15", 3: "10"},
            {(4, 25): "100", (26, 50): "100", (51, 100): "200", (101, 500): "300", (501, 1500): "200"},
        )

        self.assertEqual(payload["total_coins"], 1000)
        self.assertEqual(payload["duration_minutes"], 720)
        self.assertEqual(len(payload["tiers"]), 8)
        self.assertEqual(payload["tiers"][0], {"position_min": 1, "position_max": 1, "coins_awarded": 20})
        self.assertEqual(payload["tiers"][3]["coins_awarded"], 100)

    def test_admin_event_requires_each_individual_top_award(self):
        with self.assertRaisesRegex(ValueError, "puesto 2"):
            _build_admin_event_payload(
                "Racer", date(2026, 9, 21), "1000", {1: "20", 2: "", 3: "10"},
                {bracket: "0" for bracket in ((4, 25), (26, 50), (51, 100), (101, 500), (501, 1500))},
            )

    def test_admin_event_rejects_tier_sum_over_pool(self):
        with self.assertRaisesRegex(ValueError, "no puede superar"):
            _build_admin_event_payload(
                "Racer", date(2026, 9, 21), "100",
                {1: "20", 2: "15", 3: "10"},
                {bracket: "30" for bracket in ((4, 25), (26, 50), (51, 100), (101, 500), (501, 1500))},
            )

    def test_admin_event_rejects_game_outside_confirmed_rotation(self):
        with self.assertRaisesRegex(ValueError, "corresponde a"):
            _build_admin_event_payload(
                "Racer", date(2026, 4, 6), "1000",
                {1: "20", 2: "15", 3: "10"},
                {(4, 25): "100", (26, 50): "100", (51, 100): "200", (101, 500): "300", (501, 1500): "200"},
            )

    def test_admin_observation_fields_keep_individual_coins_separate_and_optional(self):
        observation = _build_admin_observation_payload("25", "123", "", "medio", "")

        self.assertEqual(observation["position"], 25)
        self.assertIsNone(observation["losses"])
        self.assertIsNone(observation["coins_earned"])

    def test_admin_observation_requires_victories(self):
        with self.assertRaisesRegex(ValueError, "victorias"):
            _build_admin_observation_payload("25", "", "", None, "")

    def test_private_registry_is_outside_python_package(self):
        registry = _local_registry_path()

        self.assertEqual(registry.parts[-3:], ("data", "private", "registro_juegos.txt"))
        self.assertTrue(registry.is_file())

    @patch("atlas_planner.app.sys.platform", "emscripten")
    def test_browser_never_loads_private_local_training_archive(self):
        with patch("atlas_planner.app.load_records") as load_records:
            self.assertEqual(_load_local_records("private-registry.txt"), [])
        load_records.assert_not_called()

    def test_profile_payload_includes_username_and_parcel_data(self):
        profile = _build_profile_payload(
            "user-1", "atlas_user", {"common": 10, "rare": 2, "epic": 1, "legendary": 0}, 3, 22, 64
        )

        self.assertEqual(profile["username"], "atlas_user")
        self.assertEqual(profile["common_parcels"], 10)
        self.assertEqual(profile["srb_boost_hours_per_month"], 64)

    def test_profile_payload_rejects_invalid_username(self):
        with self.assertRaisesRegex(ValueError, "nombre de usuario"):
            _build_profile_payload(
                "user-1", "x", {"common": 0, "rare": 0, "epic": 0, "legendary": 0}, 0, 0, 0
            )

    def test_parses_new_tournament_format_and_ignores_player_label(self):
        rows = _admin_import_rows("Racer 21/09/2026 25 123 1000 200 720 4 medio")

        self.assertEqual(rows[0]["event_date"], "2026-09-21")
        self.assertEqual(rows[0]["duration_minutes"], 720)
        self.assertEqual(rows[0]["losses"], 4)
        self.assertEqual(rows[0]["activity_phase"], "medio")
        self.assertNotIn("player", rows[0])

    def test_rejects_more_than_ten_losses_in_admin_import(self):
        with self.assertRaisesRegex(ValueError, "derrotas"):
            _admin_import_rows("Racer 21/09/2026 25 123 1000 200 720 11 NA")

    def test_rejects_non_event_weekday(self):
        with self.assertRaisesRegex(ValueError, "torneos regulares"):
            _admin_import_rows("Racer 23-09-2026 25 123 1000 200 180 NA")

    def test_discontinued_saturday_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "ya no se celebran"):
            _admin_import_rows("Racer 26-09-2026 25 123 1000 200 720 NA NA")

    def test_training_observations_are_mapped_without_identity(self):
        records = _training_records(
            [{
                "position": 25,
                "victories": 123,
                "coins_earned": 200,
                "event": {
                    "game": "Racer",
                    "event_date": "2026-09-21",
                    "duration_minutes": 720,
                    "total_coins": 1000,
                },
            }]
        )

        self.assertEqual(records[0].position, 25)
        self.assertIsNone(records[0].player)

    @patch("atlas_planner.supabase_api.urlopen")
    def test_admin_import_returns_inserted_count(self, mocked_urlopen):
        response = BytesIO(b"2")
        response.__enter__ = lambda obj: obj
        response.__exit__ = lambda *args: None
        mocked_urlopen.return_value = response

        count = SupabaseConfig("https://example.supabase.co", "sb_publishable_test").import_observations(
            [{"game": "Racer"}], "user-access-token"
        )

        self.assertEqual(count, 2)
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("admin_import_model_rows", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-access-token")


if __name__ == "__main__":
    unittest.main()
