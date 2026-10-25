import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas_planner.atlas_income import (
    BADGE_BONUS_TIERS,
    SPAIN_BOOST_TIERS,
    badge_bonus_percent,
    calculate_income,
    normal_boost_multiplier,
)
from atlas_planner.minigame_model import (
    GameRecord,
    TARGET_POSITIONS,
    backtest_by_event,
    estimate_victories_for_position,
    estimate_event_pool,
    infer_event_duration_minutes,
    predict_event,
)


def make_records():
    records = []
    for week in range(5):
        event_date = date(2026, 6, 1) + timedelta(days=week * 7)
        for position, victories in zip(TARGET_POSITIONS, (100, 80, 60, 40, 20)):
            records.append(
                GameRecord(
                    game="Racer",
                    event_date=event_date,
                    position=position,
                    victories=victories + week,
                    total_coins=100_000 + week * 1_000,
                    coins_earned=0,
                    played_minutes=None,
                    duration_minutes=720,
                    player=None,
                )
            )
    return records


class AtlasIncomeTests(unittest.TestCase):
    def test_normal_boost_tier_boundaries(self):
        for minimum, maximum, multiplier in SPAIN_BOOST_TIERS:
            self.assertEqual(normal_boost_multiplier(minimum), multiplier)
            if maximum is not None:
                self.assertEqual(normal_boost_multiplier(maximum), multiplier)
                self.assertEqual(
                    normal_boost_multiplier(maximum + 1),
                    next(
                        tier[2]
                        for tier in SPAIN_BOOST_TIERS
                        if tier[0] == maximum + 1
                    ),
                )
        self.assertEqual(normal_boost_multiplier(0), 0)

    def test_badge_bonus_tier_boundaries(self):
        for index, (minimum, bonus) in enumerate(BADGE_BONUS_TIERS):
            self.assertEqual(badge_bonus_percent(minimum), bonus)
            if index:
                self.assertEqual(
                    badge_bonus_percent(minimum - 1),
                    BADGE_BONUS_TIERS[index - 1][1],
                )

    def test_income_hours_include_unboosted_time_and_zero_srb(self):
        result = calculate_income(
            {"common": 10},
            badge_count=0,
            period_days=1,
            normal_boost_hours_per_day=22,
            srb_boost_hours_per_month=0,
        )
        self.assertAlmostEqual(result["total_hours"], 24)
        expected_normal = 22 * (24 - result["srb_window_hours"]) / 24
        self.assertAlmostEqual(result["normal_active_hours"], expected_normal)
        self.assertAlmostEqual(result["unboosted_hours"], 24 - expected_normal)
        self.assertEqual(result["srb_active_hours"], 0)
        self.assertGreater(result["gross_usd"], 0)


class MinigameModelTests(unittest.TestCase):
    def test_regular_tournament_duration_and_last_saturdays_are_excluded(self):
        self.assertEqual(infer_event_duration_minutes("2026-08-03", "Racer"), 720)
        with self.assertRaisesRegex(ValueError, "ya no se celebran"):
            infer_event_duration_minutes("2026-08-29", "Racer")

    def test_legacy_history_is_excluded_from_regular_tournament_predictions(self):
        records = [replace(record, duration_minutes=180) for record in make_records()]
        with self.assertRaisesRegex(ValueError, "no earlier active history"):
            estimate_event_pool(records, "Racer", "2026-08-03", 720)

    def test_archived_twelve_hour_rows_are_excluded(self):
        records = [replace(record, archived=True) for record in make_records()]
        with self.assertRaisesRegex(ValueError, "no earlier active history"):
            estimate_event_pool(records, "Racer", "2026-08-03", 720)

    def test_1500_prediction_uses_standard_median_for_even_history(self):
        records = []
        for day, wins in ((1, 10), (8, 20)):
            records.append(
                GameRecord(
                    game="Racer",
                    event_date=date(2026, 6, day),
                    position=1500,
                    victories=wins,
                    total_coins=100_000,
                    coins_earned=0,
                    played_minutes=None,
                    duration_minutes=720,
                    player=None,
                )
            )
        estimate = estimate_victories_for_position(
            records, "Racer", 1500, 100_000, "2026-06-15", 720
        )
        self.assertEqual(estimate["estimated_victories"], 15)

    def test_prediction_thresholds_are_monotonic_and_keep_regular_duration(self):
        prediction = predict_event(make_records(), "Racer", "2026-08-03")
        wins = [
            prediction["thresholds"][position]["estimated_victories"]
            for position in TARGET_POSITIONS
        ]
        self.assertEqual(wins, sorted(wins, reverse=True))
        self.assertEqual(prediction["duration_minutes"], 720)
        self.assertNotIn("time", prediction["thresholds"][25])

    def test_backtest_1500_matches_its_historical_median_baseline(self):
        result = backtest_by_event(make_records(), min_prior_events=1)
        self.assertEqual(
            result["exact_position_sample_count_by_position"][1500],
            4,
        )
        self.assertAlmostEqual(
            result["exact_position_victory_mae_by_position"][1500],
            result["historical_same_position_victory_mae"][1500],
        )


if __name__ == "__main__":
    unittest.main()
