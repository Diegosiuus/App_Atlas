"""Mobile-first PWA prototype for Atlas Earth estimates."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import flet as ft

from atlas_income import (
    AVERAGE_DAYS_PER_MONTH,
    RARITIES,
    calculate_income,
    recommend_purchase_strategy,
    simulate_purchase,
)
from minigame_model import (
    ACTIVE_GAMES,
    TARGET_POSITIONS,
    NORMAL_EVENT_WEEKDAYS,
    is_last_saturday,
    load_records,
    predict_event,
)


INK = "#1F2A2E"
MUTED = "#667579"
CANVAS = "#F2F6F5"
SURFACE = "#FFFFFF"
TEAL = "#087F72"
AMBER = "#C77B14"
RED = "#A83B3B"
BORDER = "#DCE5E2"


def _parse_number(value: str, label: str, integer: bool = False) -> int | float:
    normalized = value.strip().replace(",", ".")
    if not normalized:
        return 0 if integer else 0.0
    try:
        number = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{label}: introduce un número válido.") from exc
    if number < 0:
        raise ValueError(f"{label}: no puede ser negativo.")
    if integer and not number.is_integer():
        raise ValueError(f"{label}: debe ser un número entero.")
    return int(number) if integer else number


def _money(value: float | int) -> str:
    return f"${value:,.2f}"


def _next_event_date(today: date | None = None) -> date:
    start = today or date.today()
    for offset in range(15):
        candidate = start + timedelta(days=offset)
        if candidate.weekday() in NORMAL_EVENT_WEEKDAYS or is_last_saturday(candidate):
            return candidate
    raise RuntimeError("No event date found in the next two weeks")


def _section_heading(title: str, subtitle: str | None = None) -> ft.Control:
    controls: list[ft.Control] = [
        ft.Text(title, size=21, weight=ft.FontWeight.BOLD, color=INK),
    ]
    if subtitle:
        controls.append(ft.Text(subtitle, size=13, color=MUTED))
    return ft.Column(controls=controls, spacing=4)


def _panel(content: ft.Control) -> ft.Control:
    return ft.Container(
        content=content,
        bgcolor=SURFACE,
        border=ft.Border.all(1, BORDER),
        border_radius=8,
        padding=16,
    )


def _number_field(label: str, value: str = "0") -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        keyboard_type=ft.KeyboardType.NUMBER,
        expand=True,
        dense=True,
    )


def main(page: ft.Page) -> None:
    page.title = "Atlas Planner"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=TEAL, use_material3=True)
    page.bgcolor = CANVAS
    page.padding = 0
    data_path = Path(__file__).resolve().with_name("registro_juegos.txt")
    try:
        records = load_records(data_path)
        data_error = None
    except (OSError, ValueError) as exc:
        records = []
        data_error = str(exc)

    rarity_fields = {
        rarity: _number_field(
            {"common": "Comunes", "rare": "Raras", "epic": "Épicas", "legendary": "Legendarias"}[rarity]
        )
        for rarity in RARITIES
    }
    badge_field = _number_field("Insignias")
    normal_hours_field = _number_field("Boost normal (h/día)", "22")
    srb_hours_field = _number_field("SRB activo (h/mes)")
    purchase_field = _number_field("Parcelas adicionales")
    income_feedback = ft.Column(spacing=10)

    game_field = ft.Dropdown(
        label="Minijuego",
        value="Racer",
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    date_field = ft.TextField(label="Fecha del evento", value=_next_event_date().isoformat(), expand=True)
    target_field = ft.Dropdown(
        label="Puesto objetivo",
        value="25",
        options=[ft.DropdownOption(key=str(position), text=str(position)) for position in TARGET_POSITIONS],
        expand=True,
    )
    player_field = ft.TextField(
        label="Jugador (opcional, para estimar tiempo)",
        hint_text="Debe coincidir con el nombre del histórico",
    )
    game_feedback = ft.Column(spacing=10)

    def income_result(e: ft.Event[ft.Button]) -> None:
        income_feedback.controls.clear()
        try:
            parcel_counts = {
                rarity: _parse_number(field.value or "0", field.label or rarity, integer=True)
                for rarity, field in rarity_fields.items()
            }
            badges = _parse_number(badge_field.value or "0", "Insignias", integer=True)
            normal_hours = _parse_number(normal_hours_field.value or "0", "Boost normal")
            srb_hours = _parse_number(srb_hours_field.value or "0", "SRB activo")
            additions = _parse_number(purchase_field.value or "0", "Parcelas adicionales", integer=True)

            income = calculate_income(
                parcel_counts,
                int(badges),
                AVERAGE_DAYS_PER_MONTH,
                float(normal_hours),
                float(srb_hours),
            )
            income_feedback.controls.append(
                _panel(
                    ft.Column(
                        controls=[
                            ft.Text("Ingreso bruto mensual estimado", size=13, color=MUTED),
                            ft.Text(_money(income["gross_usd"]), size=30, weight=ft.FontWeight.BOLD, color=TEAL),
                            ft.Text(
                                f"{income['parcel_count']} parcelas · boost normal x{income['normal_boost_multiplier']} · insignias +{income['badge_bonus_percent']}%",
                                size=13,
                                color=INK,
                            ),
                        ],
                        spacing=5,
                    )
                )
            )
            income_feedback.controls.append(
                ft.Column(
                    controls=[
                        _section_heading("Desglose bruto"),
                        _metric_row("Boost normal", _money(income["normal_boost_usd"])),
                        _metric_row("SRB x50", _money(income["srb_usd"])),
                        _metric_row("Horas sin boost · x1", _money(income["unboosted_usd"])),
                        ft.Text(
                            f"Supuesto: {income['srb_active_hours']:.1f} h de SRB activas al mes; las no activas dentro de la ventana SRB se cuentan a x1.",
                            size=12,
                            color=MUTED,
                        ),
                    ],
                    spacing=8,
                )
            )

            if additions > 0:
                purchase = simulate_purchase(
                    parcel_counts,
                    int(additions),
                    int(badges),
                    AVERAGE_DAYS_PER_MONTH,
                    float(normal_hours),
                    float(srb_hours),
                )
                income_feedback.controls.append(
                    _panel(
                        ft.Column(
                            controls=[
                                _section_heading(f"Con {int(additions)} parcelas más"),
                                _metric_row("Nuevo ingreso mensual", _money(purchase["after_purchase"]["gross_usd"])),
                                _metric_row("Variación", f"+{_money(purchase['delta_usd'])} ({purchase['delta_percent']:.1f}%)"),
                                ft.Text("Parcelas nuevas estimadas con rareza promedio.", size=12, color=MUTED),
                            ],
                            spacing=8,
                        )
                    )
                )

            if sum(parcel_counts.values()) > 0:
                advice = recommend_purchase_strategy(
                    parcel_counts,
                    int(badges),
                    AVERAGE_DAYS_PER_MONTH,
                    float(normal_hours),
                    float(srb_hours),
                )
                if advice["status"] == "estimate":
                    message = (
                        f"Compra hasta {advice['tier_upper']} para completar el tramo; "
                        f"luego espera aproximadamente {advice['wait_after_tier_upper']} parcelas "
                        f"más para alcanzar {advice['break_even_total']} antes del siguiente salto."
                    )
                else:
                    message = str(advice["message"])
                income_feedback.controls.append(
                    _panel(
                        ft.Column(
                            controls=[
                                _section_heading("Estrategia por tramos"),
                                ft.Text(message, color=INK),
                                ft.Text(str(advice.get("message", "")), size=12, color=MUTED),
                            ],
                            spacing=8,
                        )
                    )
                )
        except ValueError as exc:
            income_feedback.controls.append(ft.Text(str(exc), color=RED))
        page.update()

    def game_result(e: ft.Event[ft.Button]) -> None:
        game_feedback.controls.clear()
        if data_error:
            game_feedback.controls.append(ft.Text(f"No se pudo leer el registro: {data_error}", color=RED))
            page.update()
            return
        try:
            prediction = predict_event(
                records,
                game_field.value or "Racer",
                date_field.value or "",
                player=(player_field.value or "").strip() or None,
            )
            pool = prediction["pool"]
            selected_position = int(target_field.value or "25")
            selected = prediction["thresholds"][selected_position]
            duration_hours = prediction["duration_minutes"] / 60

            game_feedback.controls.append(
                _panel(
                    ft.Column(
                        controls=[
                            ft.Text(f"Bolsa estimada · {_money(pool['estimated_coins'])} monedas", size=19, weight=ft.FontWeight.BOLD, color=INK),
                            ft.Text(
                                f"Rango histórico: {pool['lower_estimate']:,}–{pool['upper_estimate']:,} · {pool['sample_events']} eventos comparables",
                                size=13,
                                color=MUTED,
                            ),
                            ft.Text(
                                f"Evento de {duration_hours:g} h · confianza {pool['confidence']}",
                                size=12,
                                color=AMBER if pool["confidence"] == "low" else TEAL,
                            ),
                        ],
                        spacing=6,
                    )
                )
            )
            game_feedback.controls.append(
                _panel(
                    ft.Column(
                        controls=[
                            ft.Text(f"Objetivo: puesto {selected_position}", size=13, color=MUTED),
                            ft.Text(f"{selected['estimated_victories']} victorias estimadas", size=27, weight=ft.FontWeight.BOLD, color=TEAL),
                            ft.Text(
                                f"Rango orientativo: {selected['lower_estimate']}–{selected['upper_estimate']} · "
                                f"basado en {selected['sample_events']} eventos comparables",
                                size=13,
                                color=INK,
                            ),
                            _time_text(selected.get("time")),
                        ],
                        spacing=7,
                    )
                )
            )
            game_feedback.controls.append(
                ft.Column(
                    controls=[
                        _section_heading("Todos los objetivos"),
                        *[
                            _metric_row(
                                f"Puesto {position}",
                                f"{prediction['thresholds'][position]['estimated_victories']} victorias",
                            )
                            for position in TARGET_POSITIONS
                        ],
                    ],
                    spacing=8,
                )
            )
            if prediction["duration_minutes"] == 180 or pool["confidence"] == "low":
                game_feedback.controls.append(
                    ft.Text(
                        "Estimación orientativa: hay pocos eventos comparables y el histórico de 3 h todavía es limitado.",
                        size=12,
                        color=AMBER,
                    )
                )
        except (ValueError, KeyError) as exc:
            game_feedback.controls.append(ft.Text(str(exc), color=RED))
        page.update()

    income_view = ft.Column(
        controls=[
            _section_heading("Ingresos", "Media mensual bruta en USD"),
            ft.Text("Parcelas por rareza", size=14, weight=ft.FontWeight.BOLD, color=INK),
            ft.Row(controls=[rarity_fields["common"], rarity_fields["rare"]], spacing=10),
            ft.Row(controls=[rarity_fields["epic"], rarity_fields["legendary"]], spacing=10),
            ft.Row(controls=[badge_field, normal_hours_field], spacing=10),
            srb_hours_field,
            ft.Text(
                "Introduce las horas x50 que realmente prevés activar (0–64 h/mes). Las horas no impulsadas se calculan a x1.",
                size=12,
                color=MUTED,
            ),
            purchase_field,
            ft.Button("Calcular ingresos", icon=ft.Icons.CALCULATE, on_click=income_result),
            income_feedback,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    minigame_view = ft.Column(
        controls=[
            _section_heading("Minijuegos", "Victorias orientativas para alcanzar el puesto"),
            ft.Row(controls=[game_field, target_field], spacing=10),
            date_field,
            player_field,
            ft.Text(
                "La duración se infiere del calendario: sábados finales 1 h; resto de eventos 3 h.",
                size=12,
                color=MUTED,
            ),
            ft.Button("Calcular predicción", icon=ft.Icons.INSIGHTS, on_click=game_result),
            game_feedback,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    active_view = 0
    nav_buttons: list[ft.Button] = []

    def select_view(index: int) -> Callable[[ft.Event[ft.Button]], None]:
        def handler(e: ft.Event[ft.Button]) -> None:
            nonlocal active_view
            active_view = index
            income_view.visible = index == 0
            minigame_view.visible = index == 1
            for button_index, button in enumerate(nav_buttons):
                button.style = ft.ButtonStyle(
                    bgcolor=TEAL if button_index == index else "transparent",
                    color="white" if button_index == index else INK,
                )
            page.update()

        return handler

    nav_buttons.extend(
        [
            ft.Button(
                "Ingresos",
                icon=ft.Icons.MONETIZATION_ON,
                style=ft.ButtonStyle(bgcolor=TEAL, color="white"),
                on_click=select_view(0),
                expand=True,
            ),
            ft.Button(
                "Minijuegos",
                icon=ft.Icons.SPORTS_ESPORTS,
                style=ft.ButtonStyle(bgcolor="transparent", color=INK),
                on_click=select_view(1),
                expand=True,
            ),
        ]
    )
    minigame_view.visible = False

    page.add(
        ft.SafeArea(
            expand=True,
            content=ft.Column(
                controls=[
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=18, vertical=12),
                        bgcolor=SURFACE,
                        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
                        content=ft.Row(
                            controls=[
                                ft.Column(
                                    controls=[
                                        ft.Text("ATLAS PLANNER", size=12, weight=ft.FontWeight.BOLD, color=TEAL),
                                        ft.Text("Tu estrategia, en números", size=12, color=MUTED),
                                    ],
                                    spacing=1,
                                ),
                                ft.Container(expand=True),
                                ft.Text(f"{len(records)} datos", size=11, color=MUTED),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                    ),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=16, vertical=10),
                        content=ft.Row(controls=nav_buttons, spacing=8),
                    ),
                    ft.Container(
                        content=ft.Column(controls=[income_view, minigame_view], expand=True),
                        padding=ft.Padding.symmetric(horizontal=16, vertical=4),
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
        )
    )


def _metric_row(label: str, value: str) -> ft.Control:
    return ft.Row(
        controls=[
            ft.Text(label, color=MUTED, expand=True),
            ft.Text(value, color=INK, weight=ft.FontWeight.W_500),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )


def _time_text(value: object) -> ft.Control:
    if not isinstance(value, dict):
        return ft.Text("Tiempo personal: sin sesiones históricas para este jugador y juego.", size=12, color=MUTED)
    return ft.Text(
        "Tiempo personal estimado: "
        f"{value['central_minutes']} min (rango {value['optimistic_minutes']}–{value['pessimistic_minutes']} min), "
        f"con {value['pace_sessions']} sesiones.",
        size=12,
        color=INK,
    )


if __name__ == "__main__":
    ft.run(main)
