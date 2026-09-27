import unittest
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
    estimate_saved_session_pace,
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
                    duration_minutes=120,
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
    def test_last_saturday_uses_fishing_vintage_not_regular_fishing(self):
        self.assertEqual(infer_event_duration_minutes("2026-08-29", "Fishing(V)"), 60)
        with self.assertRaises(ValueError):
            infer_event_duration_minutes("2026-08-29", "Fishing")

    def test_two_hour_history_is_duration_normalized_with_wider_range(self):
        records = make_records()
        pool = estimate_event_pool(records, "Racer", "2026-08-03", 180)
        target = estimate_victories_for_position(
            records, "Racer", 25, 150_000, "2026-08-03", 180
        )
        self.assertEqual(pool["estimated_coins"], 151_500)
        self.assertEqual(target["estimated_victories"], 152)
        self.assertLessEqual(target["lower_estimate"], round(target["estimated_victories"] * 0.8))
        self.assertGreaterEqual(target["upper_estimate"], round(target["estimated_victories"] * 1.2))

    def test_saved_session_pace_uses_only_the_selected_game(self):
        pace = estimate_saved_session_pace(
            [
                {"game": "Racer", "victories": 20, "played_minutes": 10},
                {"game": "Racer", "victories": 40, "played_minutes": 10},
                {"game": "Golf", "victories": 100, "played_minutes": 1},
            ],
            "Racer",
        )
        self.assertEqual(pace["sample_sessions"], 2)
        self.assertEqual(pace["median_victories_per_minute"], 3)

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
                    duration_minutes=120,
                    player=None,
                )
            )
        estimate = estimate_victories_for_position(
            records, "Racer", 1500, 100_000, "2026-06-15", 120
        )
        self.assertEqual(estimate["estimated_victories"], 15)

    def test_prediction_thresholds_are_monotonic_and_3h_is_low_confidence(self):
        prediction = predict_event(make_records(), "Racer", "2026-08-03")
        wins = [
            prediction["thresholds"][position]["estimated_victories"]
            for position in TARGET_POSITIONS
        ]
        self.assertEqual(wins, sorted(wins, reverse=True))
        self.assertTrue(prediction["pool"]["duration_mismatch"])
        self.assertEqual(prediction["pool"]["confidence"], "low")

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
