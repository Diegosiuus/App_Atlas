"""Gross Atlas Earth rent estimates for the initial Spain-only version."""

from __future__ import annotations

from math import ceil
from typing import Mapping


PARCEL_RENT_PER_SECOND = {
    "common": 0.0000000011,
    "rare": 0.0000000016,
    "epic": 0.0000000022,
    "legendary": 0.0000000044,
}

RARITY_PROBABILITIES = {
    "common": 0.50,
    "rare": 0.30,
    "epic": 0.15,
    "legendary": 0.05,
}

BADGE_BONUS_TIERS = (
    (0, 0),
    (1, 5),
    (11, 10),
    (31, 15),
    (61, 20),
    (101, 25),
)

# (minimum parcel count, maximum parcel count, normal boost multiplier)
SPAIN_BOOST_TIERS = (
    (1, 70, 20),
    (71, 100, 15),
    (101, 135, 10),
    (136, 170, 8),
    (171, 200, 7),
    (201, 250, 6),
    (251, 300, 5),
    (301, 350, 4),
    (351, 400, 3),
    (401, None, 2),
)

SRB_WINDOW_HOURS_PER_MONTH = 64.0
AVERAGE_DAYS_PER_MONTH = 365.2425 / 12
SECONDS_PER_HOUR = 60 * 60
HOURS_PER_DAY = 24

RARITIES = tuple(PARCEL_RENT_PER_SECOND)


def _validate_parcels(parcel_counts: Mapping[str, int]) -> dict[str, int]:
    counts = {rarity: parcel_counts.get(rarity, 0) for rarity in RARITIES}
    for rarity, count in counts.items():
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"{rarity} parcel count must be a non-negative integer")
    return counts


def total_parcels(parcel_counts: Mapping[str, int]) -> int:
    return sum(_validate_parcels(parcel_counts).values())


def badge_bonus_percent(badge_count: int) -> int:
    if not isinstance(badge_count, int) or badge_count < 0:
        raise ValueError("badge_count must be a non-negative integer")
    for minimum, bonus in reversed(BADGE_BONUS_TIERS):
        if badge_count >= minimum:
            return bonus
    return 0


def normal_boost_multiplier(parcel_count: int) -> int:
    if not isinstance(parcel_count, int) or parcel_count < 0:
        raise ValueError("parcel_count must be a non-negative integer")
    if parcel_count == 0:
        return 0
    for minimum, maximum, multiplier in SPAIN_BOOST_TIERS:
        if parcel_count >= minimum and (maximum is None or parcel_count <= maximum):
            return multiplier
    raise ValueError("parcel count does not fall within a configured Spain boost tier")


def average_parcel_rent_per_second(
    probabilities: Mapping[str, float] = RARITY_PROBABILITIES,
) -> float:
    weights = {rarity: probabilities.get(rarity, 0.0) for rarity in RARITIES}
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("rarity probabilities cannot be negative")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("rarity probabilities must sum to 1")
    return sum(PARCEL_RENT_PER_SECOND[r] * weights[r] for r in RARITIES)


def _parcel_rent_per_second(parcel_counts: Mapping[str, int]) -> float:
    counts = _validate_parcels(parcel_counts)
    return sum(PARCEL_RENT_PER_SECOND[r] * counts[r] for r in RARITIES)


def _period_hours(
    period_days: float,
    normal_boost_hours_per_day: float,
    srb_boost_hours_per_month: float,
) -> dict[str, float]:
    if period_days <= 0:
        raise ValueError("period_days must be greater than 0")
    if not 0 <= normal_boost_hours_per_day <= HOURS_PER_DAY:
        raise ValueError("normal_boost_hours_per_day must be between 0 and 24")
    if not 0 <= srb_boost_hours_per_month <= SRB_WINDOW_HOURS_PER_MONTH:
        raise ValueError("srb_boost_hours_per_month must be between 0 and 64")

    total_hours = period_days * HOURS_PER_DAY
    months = period_days / AVERAGE_DAYS_PER_MONTH
    srb_window_hours = min(total_hours, SRB_WINDOW_HOURS_PER_MONTH * months)
    srb_active_hours = min(srb_boost_hours_per_month * months, srb_window_hours)

    # Exact SRB dates are unknown, so normal boost coverage is prorated over
    # the time outside the monthly SRB windows.
    regular_window_hours = total_hours - srb_window_hours
    normal_active_hours = min(
        normal_boost_hours_per_day * regular_window_hours / HOURS_PER_DAY,
        regular_window_hours,
    )
    unboosted_hours = total_hours - normal_active_hours - srb_active_hours

    return {
        "total_hours": total_hours,
        "srb_window_hours": srb_window_hours,
        "srb_active_hours": srb_active_hours,
        "normal_active_hours": normal_active_hours,
        "unboosted_hours": unboosted_hours,
    }


def _income_from_rate(
    parcel_rent_per_second: float,
    parcel_count: int,
    badge_count: int,
    period_days: float,
    normal_boost_hours_per_day: float,
    srb_boost_hours_per_month: float,
) -> dict[str, float | int]:
    hours = _period_hours(
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )
    normal_multiplier = normal_boost_multiplier(parcel_count)
    badge_percent = badge_bonus_percent(badge_count)
    badge_factor = 1 + badge_percent / 100
    scale = parcel_rent_per_second * SECONDS_PER_HOUR * badge_factor

    unboosted_income = scale * hours["unboosted_hours"]
    normal_income = (
        scale * hours["normal_active_hours"] * normal_multiplier
    )
    srb_income = scale * hours["srb_active_hours"] * 50

    return {
        "gross_usd": unboosted_income + normal_income + srb_income,
        "unboosted_usd": unboosted_income,
        "normal_boost_usd": normal_income,
        "srb_usd": srb_income,
        "parcel_count": parcel_count,
        "normal_boost_multiplier": normal_multiplier,
        "badge_bonus_percent": badge_percent,
        **hours,
    }


def calculate_income(
    parcel_counts: Mapping[str, int],
    badge_count: int,
    period_days: float,
    normal_boost_hours_per_day: float,
    srb_boost_hours_per_month: float,
) -> dict[str, float | int]:
    """Estimate gross USD income for a known parcel portfolio.

    SRB hours are user-active x50 hours per average month, from 0 to 64.
    Remaining SRB-window hours earn at x1. Normal boost hours are counted
    outside SRB windows; hours without either boost earn at x1.
    """
    counts = _validate_parcels(parcel_counts)
    return _income_from_rate(
        _parcel_rent_per_second(counts),
        sum(counts.values()),
        badge_count,
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )


def simulate_purchase(
    parcel_counts: Mapping[str, int],
    additional_parcels: int,
    badge_count: int,
    period_days: float,
    normal_boost_hours_per_day: float,
    srb_boost_hours_per_month: float,
    probabilities: Mapping[str, float] = RARITY_PROBABILITIES,
) -> dict[str, float | int | dict[str, float | int]]:
    """Compare current income with the expected result after buying X parcels."""
    if not isinstance(additional_parcels, int) or additional_parcels < 0:
        raise ValueError("additional_parcels must be a non-negative integer")

    counts = _validate_parcels(parcel_counts)
    current_rate = _parcel_rent_per_second(counts)
    current_total = sum(counts.values())
    average_rate = average_parcel_rent_per_second(probabilities)
    after = _income_from_rate(
        current_rate + additional_parcels * average_rate,
        current_total + additional_parcels,
        badge_count,
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )
    current = _income_from_rate(
        current_rate,
        current_total,
        badge_count,
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )
    delta = float(after["gross_usd"]) - float(current["gross_usd"])
    return {
        "current": current,
        "after_purchase": after,
        "additional_parcels": additional_parcels,
        "delta_usd": delta,
        "delta_percent": (
            delta / float(current["gross_usd"]) * 100
            if float(current["gross_usd"]) > 0
            else 0.0
        ),
        "rarity_assumption": dict(probabilities),
    }


def recommend_purchase_strategy(
    parcel_counts: Mapping[str, int],
    badge_count: int,
    period_days: float,
    normal_boost_hours_per_day: float,
    srb_boost_hours_per_month: float,
    probabilities: Mapping[str, float] = RARITY_PROBABILITIES,
) -> dict[str, int | float | None | str]:
    """Find income-only batch targets around the next parcel boost drop."""
    counts = _validate_parcels(parcel_counts)
    current_total = sum(counts.values())
    if current_total == 0:
        raise ValueError("at least one current parcel is needed for this recommendation")

    current_tier_index = next(
        i for i, (minimum, maximum, _) in enumerate(SPAIN_BOOST_TIERS)
        if current_total >= minimum and (maximum is None or current_total <= maximum)
    )
    _, tier_upper, _ = SPAIN_BOOST_TIERS[current_tier_index]
    if tier_upper is None:
        return {
            "status": "no_future_boost_drop",
            "current_total": current_total,
            "message": "A partir de 401 parcelas el boost normal permanece en x2.",
            "break_even_total": None,
            "buy_to_tier_upper": 0,
            "wait_after_tier_upper": 0,
            "wait_before_any_purchase": 0,
        }

    average_rate = average_parcel_rent_per_second(probabilities)
    current_rate = _parcel_rent_per_second(counts)
    reference_additions = tier_upper - current_total
    reference_rate = current_rate + reference_additions * average_rate
    hours = _period_hours(
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )
    badge_factor = 1 + badge_bonus_percent(badge_count) / 100
    reference_income = _income_from_rate(
        reference_rate,
        tier_upper,
        badge_count,
        period_days,
        normal_boost_hours_per_day,
        srb_boost_hours_per_month,
    )["gross_usd"]
    seconds_badge_factor = SECONDS_PER_HOUR * badge_factor

    break_even_total = None
    for minimum, maximum, multiplier in SPAIN_BOOST_TIERS[current_tier_index + 1 :]:
        effective_hours = (
            hours["unboosted_hours"]
            + hours["normal_active_hours"] * multiplier
            + hours["srb_active_hours"] * 50
        )
        income_per_rate = seconds_badge_factor * effective_hours
        required_rate = float(reference_income) / income_per_rate
        needed_additions = max(
            0,
            ceil((required_rate - current_rate) / average_rate - 1e-12),
        )
        candidate = max(minimum, current_total + needed_additions)

        if maximum is None or candidate <= maximum:
            break_even_total = candidate
            break

    if break_even_total is None:
        return {
            "status": "no_break_even_found",
            "current_total": current_total,
            "tier_upper": tier_upper,
            "break_even_total": None,
            "buy_to_tier_upper": max(0, tier_upper - current_total),
            "wait_after_tier_upper": None,
            "wait_before_any_purchase": None,
        }

    return {
        "status": "estimate",
        "current_total": current_total,
        "tier_upper": tier_upper,
        "break_even_total": break_even_total,
        "buy_to_tier_upper": max(0, tier_upper - current_total),
        "wait_after_tier_upper": max(0, break_even_total - tier_upper),
        "wait_before_any_purchase": max(0, break_even_total - current_total),
        "reference_gross_usd": float(reference_income),
        "rarity_assumption": dict(probabilities),
        "message": (
            "Estimacion basada en ingresos brutos y rareza promedio; "
            "la rareza real de las parcelas nuevas puede cambiar el resultado."
        ),
    }
