"""Historical analog forecasts for Atlas Earth minigame events.

This first version uses weighted medians rather than a high-capacity ML model.
It needs only the Python standard library and keeps archived Racer(V) data out
of active estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import log
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence


TARGET_POSITIONS = (25, 50, 100, 500, 1500)
ARCHIVED_GAMES = frozenset({"Racer(V)"})
ACTIVE_GAMES = frozenset(
    {"Racer", "Fishing", "Golf", "Warship", "Bowling", "Fishing(V)"}
)
NORMAL_EVENT_WEEKDAYS = frozenset({0, 1, 3, 6})  # Monday, Tuesday, Thursday, Sunday


@dataclass(frozen=True)
class GameRecord:
    game: str
    event_date: date
    position: int
    victories: int
    total_coins: int
    coins_earned: int
    played_minutes: float | None
    duration_minutes: int
    player: str | None

    @property
    def event_key(self) -> tuple[date, str]:
        return self.event_date, self.game


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("date must use YYYY-MM-DD or DD-MM-YYYY")


def is_last_saturday(value: date | str) -> bool:
    event_date = _as_date(value)
    return event_date.weekday() == 5 and (event_date + timedelta(days=7)).month != event_date.month


def infer_event_duration_minutes(value: date | str, game: str) -> int:
    event_date = _as_date(value)
    if game in ARCHIVED_GAMES:
        raise ValueError(f"{game} is archived and excluded from active predictions")
    if game not in ACTIVE_GAMES:
        raise ValueError(f"unknown active game: {game}")
    if game == "Fishing(V)" and not is_last_saturday(event_date):
        raise ValueError("Fishing(V) is scheduled for the last Saturday of the month")
    if is_last_saturday(event_date):
        return 60
    if event_date.weekday() not in NORMAL_EVENT_WEEKDAYS:
        raise ValueError("regular minigames are scheduled Monday, Tuesday, Thursday, or Sunday")
    return 180


def load_records(file_path: str | Path) -> list[GameRecord]:
    """Read the whitespace-delimited registro_juegos.txt without changing it."""
    records: list[GameRecord] = []
    with Path(file_path).open("r", encoding="utf-8-sig") as source:
        header = source.readline().split()
        expected = [
            "Juego", "Fecha", "Posicion", "Victorias", "MonedasTotales",
            "MonedasGanadas", "TiempoJugado(min)", "Duracion(min)", "Jugador",
        ]
        if header != expected:
            raise ValueError("registro_juegos.txt has an unexpected header")

        for line_number, line in enumerate(source, start=2):
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != len(expected):
                raise ValueError(f"invalid field count on line {line_number}")

            game, raw_date, position, victories, pool, payout, played, duration, player = fields
            event_date = _as_date(raw_date)
            duration_value = None if duration.upper() == "NA" else int(duration)
            if duration_value is None:
                duration_value = infer_event_duration_minutes(event_date, game)
            records.append(
                GameRecord(
                    game=game,
                    event_date=event_date,
                    position=int(position),
                    victories=int(victories),
                    total_coins=int(pool),
                    coins_earned=int(payout),
                    played_minutes=None if played.upper() == "NA" else float(played),
                    duration_minutes=duration_value,
                    player=None if player.upper() == "NA" else player,
                )
            )
    return records


def _active_records(records: Iterable[GameRecord]) -> list[GameRecord]:
    return [record for record in records if record.game not in ARCHIVED_GAMES]


def _events(records: Iterable[GameRecord]) -> list[dict[str, object]]:
    grouped: dict[tuple[date, str], list[GameRecord]] = {}
    for record in _active_records(records):
        if record.game not in ACTIVE_GAMES:
            continue
        grouped.setdefault(record.event_key, []).append(record)

    events: list[dict[str, object]] = []
    for (event_date, game), event_records in grouped.items():
        pools = {record.total_coins for record in event_records}
        durations = {record.duration_minutes for record in event_records}
        if len(pools) != 1 or len(durations) != 1:
            raise ValueError(f"inconsistent event-level data for {game} on {event_date}")
        events.append(
            {
                "date": event_date,
                "game": game,
                "pool": pools.pop(),
                "duration": durations.pop(),
                "records": event_records,
            }
        )
    return sorted(events, key=lambda event: (event["date"], event["game"]))


def _weighted_quantile(values: Sequence[tuple[float, float]], quantile: float) -> float:
    if not values:
        raise ValueError("cannot summarize an empty sample")
    ordered = sorted(values, key=lambda pair: pair[0])
    total_weight = sum(weight for _, weight in ordered)
    threshold = total_weight * quantile
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _day_weight(past_date: date, target_date: date) -> float:
    return 1 / (1 + abs(past_date.day - target_date.day) / 8)


def _pool_weight(past_pool: int, target_pool: float) -> float:
    if past_pool <= 0 or target_pool <= 0:
        return 1.0
    return 1 / (1 + abs(log(past_pool / target_pool)))


def _duration_subset(
    events: Sequence[dict[str, object]], target_duration: int
) -> tuple[list[dict[str, object]], bool]:
    nearest_difference = min(abs(int(event["duration"]) - target_duration) for event in events)
    selected = [
        event for event in events
        if abs(int(event["duration"]) - target_duration) == nearest_difference
    ]
    return selected, nearest_difference != 0


def _confidence(sample_count: int, duration_mismatch: bool) -> str:
    if duration_mismatch or sample_count < 5:
        return "low"
    if sample_count < 12:
        return "medium"
    return "higher"


def estimate_event_pool(
    records: Iterable[GameRecord],
    game: str,
    event_date: date | str,
    duration_minutes: int | None = None,
) -> dict[str, int | float | str | bool]:
    """Estimate the event pool from one historical row per comparable event."""
    target_date = _as_date(event_date)
    if game in ARCHIVED_GAMES:
        raise ValueError(f"{game} is archived and excluded from active predictions")
    if game not in ACTIVE_GAMES:
        raise ValueError(f"unknown active game: {game}")
    target_duration = duration_minutes or infer_event_duration_minutes(target_date, game)
    history = [event for event in _events(records) if event["game"] == game]
    history = [event for event in history if event["date"] < target_date]
    if not history:
        raise ValueError(f"no earlier active history available for {game}")

    comparable, duration_mismatch = _duration_subset(history, target_duration)
    weighted = [
        (
            float(event["pool"]),
            _day_weight(event["date"], target_date),
        )
        for event in comparable
    ]
    return {
        "estimated_coins": round(_weighted_quantile(weighted, 0.5)),
        "lower_estimate": round(_weighted_quantile(weighted, 0.2)),
        "upper_estimate": round(_weighted_quantile(weighted, 0.8)),
        "sample_events": len(comparable),
        "duration_mismatch": duration_mismatch,
        "confidence": _confidence(len(comparable), duration_mismatch),
    }


def estimate_victories_for_position(
    records: Iterable[GameRecord],
    game: str,
    target_position: int,
    target_pool: float,
    event_date: date | str,
    duration_minutes: int,
) -> dict[str, int | float | str | bool]:
    """Estimate wins using weighted historical analogs grouped by event."""
    if target_position not in TARGET_POSITIONS:
        raise ValueError(f"target_position must be one of {TARGET_POSITIONS}")
    if game in ARCHIVED_GAMES or game not in ACTIVE_GAMES:
        raise ValueError(f"{game} is not an active prediction category")

    target_date = _as_date(event_date)
    history = [event for event in _events(records) if event["game"] == game]
    history = [event for event in history if event["date"] < target_date]
    if not history:
        raise ValueError(f"no earlier active history available for {game}")

    exact_history = [
        event for event in history
        if any(record.position == target_position for record in event["records"])
    ]
    if target_position == 1500 and exact_history:
        # Validation favors an exact-rank historical median over extrapolation
        # from other ranks for this sparsely sampled tail threshold.
        comparable = exact_history
        duration_mismatch = not any(
            int(event["duration"]) == duration_minutes for event in exact_history
        )
    else:
        comparable, duration_mismatch = _duration_subset(history, duration_minutes)
    has_exact_positions = any(
        any(record.position == target_position for record in event["records"])
        for event in comparable
    )
    event_estimates: list[tuple[float, float]] = []
    exact_position_events = 0
    for event in comparable:
        event_records = event["records"]
        assert isinstance(event_records, list)
        exact_records = [
            record for record in event_records if record.position == target_position
        ]
        if exact_records:
            exact_position_events += 1
            nearby = [(float(record.victories), 1.0) for record in exact_records]
            best_position_weight = 1.0
        else:
            if has_exact_positions:
                continue
            nearby = []
            for record in event_records:
                if record.position < 4 or record.position > 1500:
                    continue
                position_weight = 1 / (
                    1 + 2 * abs(log(record.position / target_position))
                )
                nearby.append((float(record.victories), position_weight))
            best_position_weight = max(
                (weight for _, weight in nearby), default=0.0
            )
        if not nearby:
            continue

        event_wins = _weighted_quantile(nearby, 0.5)
        if target_position == 1500 and exact_records:
            # The rank-matched median is more reliable than event similarity
            # weights for this sparsely sampled tail threshold.
            event_weight = 1.0
        else:
            event_weight = (
                best_position_weight
                * _pool_weight(int(event["pool"]), target_pool)
                * _day_weight(event["date"], target_date)
            )
        event_estimates.append((event_wins, event_weight))

    if not event_estimates:
        raise ValueError(f"no usable rank observations for {game}")
    central_victories = (
        median(value for value, _ in event_estimates)
        if target_position == 1500 and exact_position_events
        else _weighted_quantile(event_estimates, 0.5)
    )
    return {
        "estimated_victories": round(central_victories),
        "lower_estimate": round(_weighted_quantile(event_estimates, 0.2)),
        "upper_estimate": round(_weighted_quantile(event_estimates, 0.8)),
        "sample_events": len(event_estimates),
        "exact_position_events": exact_position_events,
        "duration_mismatch": duration_mismatch,
        "confidence": _confidence(exact_position_events, duration_mismatch),
    }


def estimate_player_pace(
    records: Iterable[GameRecord], player: str, game: str
) -> dict[str, float | int] | None:
    """Return observed victories/minute for this player and game, or None."""
    rates = [
        record.victories / record.played_minutes
        for record in records
        if record.game == game
        and record.player == player
        and record.played_minutes is not None
        and record.played_minutes > 0
    ]
    if not rates:
        return None
    ordered = sorted(rates)
    return {
        "median_victories_per_minute": median(ordered),
        "lower_pace": _weighted_quantile([(rate, 1.0) for rate in ordered], 0.25),
        "upper_pace": _weighted_quantile([(rate, 1.0) for rate in ordered], 0.75),
        "sample_sessions": len(ordered),
    }


def estimate_time_minutes(victories: int, pace: dict[str, float | int] | None) -> dict[str, int | float] | None:
    if pace is None:
        return None
    central_pace = float(pace["median_victories_per_minute"])
    slow_pace = float(pace["lower_pace"])
    fast_pace = float(pace["upper_pace"])
    if central_pace <= 0 or slow_pace <= 0 or fast_pace <= 0:
        return None
    return {
        "central_minutes": round(victories / central_pace),
        "optimistic_minutes": round(victories / fast_pace),
        "pessimistic_minutes": round(victories / slow_pace),
        "pace_sessions": int(pace["sample_sessions"]),
    }


def predict_event(
    records: Iterable[GameRecord],
    game: str,
    event_date: date | str,
    player: str | None = None,
    duration_minutes: int | None = None,
) -> dict[str, object]:
    """Forecast the pool, threshold wins, and personal time when available."""
    records = list(records)
    target_date = _as_date(event_date)
    duration = duration_minutes or infer_event_duration_minutes(target_date, game)
    pool = estimate_event_pool(records, game, target_date, duration)
    pace = estimate_player_pace(records, player, game) if player is not None else None
    raw_thresholds = {
        position: estimate_victories_for_position(
            records,
            game,
            position,
            float(pool["estimated_coins"]),
            target_date,
            duration,
        )
        for position in TARGET_POSITIONS
    }

    # More wins should be needed for a better (numerically smaller) position.
    thresholds: dict[int, dict[str, object]] = {}
    previous_wins = float("inf")
    for position in TARGET_POSITIONS:
        item = raw_thresholds[position]
        wins = min(int(item["estimated_victories"]), previous_wins)
        previous_wins = wins
        time_estimate = estimate_time_minutes(wins, pace)
        thresholds[position] = {
            **item,
            "estimated_victories": wins,
            "time": time_estimate,
        }

    return {
        "game": game,
        "date": target_date.isoformat(),
        "duration_minutes": duration,
        "pool": pool,
        "thresholds": thresholds,
        "personal_time_available": pace is not None,
    }


def _mae(errors: Sequence[float]) -> float | None:
    return sum(abs(error) for error in errors) / len(errors) if errors else None


def backtest_by_event(
    records: Iterable[GameRecord], min_prior_events: int = 10
) -> dict[str, object]:
    """Chronologically backtest pool and exact-threshold wins by whole event."""
    all_records = _active_records(records)
    events = _events(all_records)
    pool_errors: list[float] = []
    last_pool_errors: list[float] = []
    historical_pool_errors: list[float] = []
    victory_errors: dict[int, list[float]] = {position: [] for position in TARGET_POSITIONS}
    exact_position_errors: dict[int, list[float]] = {
        position: [] for position in TARGET_POSITIONS
    }
    baseline_victory_errors: dict[int, list[float]] = {
        position: [] for position in TARGET_POSITIONS
    }
    predictions: list[dict[str, object]] = []

    for event in events:
        event_date = event["date"]
        history = [record for record in all_records if record.event_date < event_date]
        prior_event_count = len(_events(history))
        if prior_event_count < min_prior_events:
            continue

        game = str(event["game"])
        duration = int(event["duration"])
        prior_game_events = [
            previous for previous in events
            if previous["game"] == game and previous["date"] < event_date
        ]
        if not prior_game_events:
            continue
        try:
            pool_forecast = estimate_event_pool(history, game, event_date, duration)
        except ValueError:
            continue
        actual_pool = int(event["pool"])
        pool_errors.append(float(pool_forecast["estimated_coins"]) - actual_pool)
        last_pool_errors.append(
            float(prior_game_events[-1]["pool"]) - actual_pool
        )
        historical_pool_errors.append(
            float(median([int(previous["pool"]) for previous in prior_game_events]))
            - actual_pool
        )
        event_rows = event["records"]
        assert isinstance(event_rows, list)
        observed_thresholds: dict[int, list[int]] = {position: [] for position in TARGET_POSITIONS}

        for record in event_rows:
            if record.position in observed_thresholds:
                observed_thresholds[record.position].append(record.victories)

        threshold_results: dict[int, dict[str, int | float]] = {}
        for position, observed in observed_thresholds.items():
            if not observed:
                continue
            try:
                forecast = estimate_victories_for_position(
                    history,
                    game,
                    position,
                    float(pool_forecast["estimated_coins"]),
                    event_date,
                    duration,
                )
            except ValueError:
                continue
            actual = round(median(observed))
            error = float(forecast["estimated_victories"] - actual)
            victory_errors[position].append(error)
            if int(forecast["exact_position_events"]) > 0:
                exact_position_errors[position].append(error)
            historical_rank_values = [
                record.victories
                for previous in prior_game_events
                for record in previous["records"]
                if record.position == position
            ]
            if historical_rank_values:
                baseline_victory_errors[position].append(
                    float(median(historical_rank_values) - actual)
                )
            threshold_results[position] = {
                "actual": actual,
                "estimated": int(forecast["estimated_victories"]),
                "error": error,
            }

        predictions.append(
            {
                "date": event_date.isoformat(),
                "game": game,
                "duration_minutes": duration,
                "pool_actual": actual_pool,
                "pool_estimated": int(pool_forecast["estimated_coins"]),
                "thresholds": threshold_results,
            }
        )

    return {
        "evaluated_events": len(predictions),
        "pool_mae": _mae(pool_errors),
        "last_same_game_pool_mae": _mae(last_pool_errors),
        "historical_same_game_median_pool_mae": _mae(historical_pool_errors),
        "victory_mae_by_position": {
            position: _mae(errors) for position, errors in victory_errors.items()
        },
        "exact_position_victory_mae_by_position": {
            position: _mae(errors) for position, errors in exact_position_errors.items()
        },
        "exact_position_sample_count_by_position": {
            position: len(errors) for position, errors in exact_position_errors.items()
        },
        "historical_same_position_victory_mae": {
            position: _mae(errors)
            for position, errors in baseline_victory_errors.items()
        },
        "predictions": predictions,
    }
