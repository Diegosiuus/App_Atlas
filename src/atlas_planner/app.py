"""Mobile-first PWA prototype for Atlas Earth estimates."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import flet as ft

from .atlas_income import (
    AVERAGE_DAYS_PER_MONTH,
    RARITIES,
    calculate_income,
    recommend_purchase_strategy,
    simulate_purchase,
)
from .minigame_model import (
    ACTIVE_GAMES,
    TARGET_POSITIONS,
    NORMAL_EVENT_WEEKDAYS,
    estimate_saved_session_pace,
    estimate_time_minutes,
    infer_event_duration_minutes,
    is_last_saturday,
    load_records,
    predict_event,
)
from .supabase_api import SupabaseConfig, SupabaseError


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


def _previous_event_date(game: str, today: date | None = None) -> date:
    start = today or date.today()
    for offset in range(15):
        candidate = start - timedelta(days=offset)
        try:
            infer_event_duration_minutes(candidate, game)
            return candidate
        except ValueError:
            continue
    raise RuntimeError("No recent event date found")


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
    supabase = SupabaseConfig.from_environment()
    session: dict[str, str] = {}
    saved_game_sessions: list[dict] = []
    data_path = Path(__file__).resolve().parent / "data" / "registro_juegos.txt"
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
        label="Jugador del histórico (opcional)",
        hint_text="Si no has iniciado sesión, usa un nombre ya presente en el registro",
    )
    game_feedback = ft.Column(spacing=10)
    result_game_field = ft.Dropdown(
        label="Minijuego",
        value="Racer",
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    result_date_field = ft.TextField(
        label="Fecha del evento",
        value=_previous_event_date("Racer").isoformat(),
        expand=True,
    )
    result_wins_field = _number_field("Victorias")
    result_position_field = _number_field("Puesto final")
    result_minutes_field = _number_field("Minutos jugados", "0")
    result_feedback = ft.Text("Inicia sesión para guardar resultados.", size=13, color=MUTED)
    result_history = ft.Column(spacing=6)
    email_field = ft.TextField(label="Correo electrónico", keyboard_type=ft.KeyboardType.EMAIL)
    password_field = ft.TextField(label="Contraseña", password=True, can_reveal_password=True)
    account_feedback = ft.Text(
        "Configura Supabase en el archivo .env para habilitar tu cuenta."
        if supabase is None
        else "Inicia sesión o crea una cuenta para sincronizar tu perfil.",
        size=13,
        color=AMBER if supabase is None else MUTED,
    )
    sign_in_button = ft.Button("Iniciar sesión", icon=ft.Icons.LOGIN, disabled=supabase is None)
    sign_up_button = ft.Button("Crear cuenta", icon=ft.Icons.PERSON_ADD, disabled=supabase is None)
    save_profile_button = ft.Button("Guardar perfil", icon=ft.Icons.SAVE, disabled=True)
    sign_out_button = ft.Button("Cerrar sesión", icon=ft.Icons.LOGOUT, disabled=True)

    def profile_payload(user_id: str) -> dict:
        counts = {
            rarity: _parse_number(field.value or "0", field.label or rarity, integer=True)
            for rarity, field in rarity_fields.items()
        }
        badges = _parse_number(badge_field.value or "0", "Insignias", integer=True)
        normal_hours = _parse_number(normal_hours_field.value or "0", "Boost normal")
        srb_hours = _parse_number(srb_hours_field.value or "0", "SRB activo")
        if normal_hours > 24:
            raise ValueError("Boost normal: el máximo es 24 horas al día.")
        if srb_hours > 64:
            raise ValueError("SRB activo: el máximo es 64 horas al mes.")
        return {
            "user_id": user_id,
            "common_parcels": counts["common"],
            "rare_parcels": counts["rare"],
            "epic_parcels": counts["epic"],
            "legendary_parcels": counts["legendary"],
            "badge_count": badges,
            "normal_boost_hours_per_day": normal_hours,
            "srb_boost_hours_per_month": srb_hours,
        }

    def fill_profile(profile: dict | None) -> None:
        profile = profile or {
            "common_parcels": 0,
            "rare_parcels": 0,
            "epic_parcels": 0,
            "legendary_parcels": 0,
            "badge_count": 0,
            "normal_boost_hours_per_day": 22,
            "srb_boost_hours_per_month": 0,
        }
        rarity_fields["common"].value = str(profile["common_parcels"])
        rarity_fields["rare"].value = str(profile["rare_parcels"])
        rarity_fields["epic"].value = str(profile["epic_parcels"])
        rarity_fields["legendary"].value = str(profile["legendary_parcels"])
        badge_field.value = str(profile["badge_count"])
        normal_hours_field.value = str(profile["normal_boost_hours_per_day"])
        srb_hours_field.value = str(profile["srb_boost_hours_per_month"])

    async def authenticate(create_account: bool) -> None:
        if supabase is None:
            return
        email = (email_field.value or "").strip().lower()
        password = password_field.value or ""
        if not email or not password:
            account_feedback.value = "Introduce tu correo y contraseña."
            page.update()
            return
        account_feedback.value = "Conectando…"
        page.update()
        try:
            response = await asyncio.to_thread(
                supabase.sign_up if create_account else supabase.sign_in,
                email,
                password,
            )
            access_token = response.get("access_token")
            user = response.get("user") or {}
            if not access_token or not user.get("id"):
                account_feedback.value = "Cuenta creada. Revisa tu correo y confirma la dirección antes de iniciar sesión."
                page.update()
                return
            session["access_token"] = access_token
            session["user_id"] = user["id"]
            session["email"] = email
            profile = await asyncio.to_thread(supabase.get_profile, user["id"], access_token)
            fill_profile(profile)
            if profile is None:
                await asyncio.to_thread(supabase.save_profile, profile_payload(user["id"]), access_token)
            try:
                saved_game_sessions[:] = await asyncio.to_thread(
                    supabase.get_game_sessions, user["id"], access_token
                )
                render_game_history()
                result_feedback.value = f"{len(saved_game_sessions)} resultados guardados en tu cuenta."
            except SupabaseError as exc:
                saved_game_sessions.clear()
                render_game_history()
                result_feedback.value = (
                    f"No se pudo cargar el historial: {exc}. "
                    "Comprueba que ejecutaste la migración 0002_player_game_results.sql."
                )
            account_feedback.value = f"Sesión iniciada como {email}. Perfil sincronizado."
            sign_in_button.disabled = True
            sign_up_button.disabled = True
            email_field.disabled = True
            password_field.disabled = True
            save_profile_button.disabled = False
            sign_out_button.disabled = False
            save_game_result_button.disabled = False
        except (SupabaseError, ValueError) as exc:
            account_feedback.value = str(exc)
        page.update()

    async def save_profile(e: ft.Event[ft.Button]) -> None:
        if supabase is None or not session:
            return
        try:
            profile = profile_payload(session["user_id"])
            account_feedback.value = "Guardando perfil…"
            page.update()
            await asyncio.to_thread(supabase.save_profile, profile, session["access_token"])
            account_feedback.value = "Perfil guardado en tu cuenta."
        except (SupabaseError, ValueError) as exc:
            account_feedback.value = str(exc)
        page.update()

    def sign_out(e: ft.Event[ft.Button]) -> None:
        session.clear()
        saved_game_sessions.clear()
        render_game_history()
        sign_in_button.disabled = supabase is None
        sign_up_button.disabled = supabase is None
        email_field.disabled = False
        password_field.disabled = False
        save_profile_button.disabled = True
        sign_out_button.disabled = True
        save_game_result_button.disabled = True
        result_feedback.value = "Inicia sesión para guardar resultados."
        account_feedback.value = "Sesión cerrada en este dispositivo."
        for index, view in enumerate(app_views):
            view.visible = index == 2
        for index, button in enumerate(nav_buttons):
            button.style = ft.ButtonStyle(
                bgcolor=TEAL if index == 2 else "transparent",
                color="white" if index == 2 else INK,
            )
        page.update()

    async def sign_in(e: ft.Event[ft.Button]) -> None:
        await authenticate(False)

    async def sign_up(e: ft.Event[ft.Button]) -> None:
        await authenticate(True)

    sign_in_button.on_click = sign_in
    sign_up_button.on_click = sign_up
    save_profile_button.on_click = save_profile
    sign_out_button.on_click = sign_out

    def render_game_history() -> None:
        result_history.controls.clear()
        if not session:
            result_history.controls.append(
                ft.Text("Inicia sesión para consultar tus partidas guardadas.", size=12, color=MUTED)
            )
            return
        if not saved_game_sessions:
            result_history.controls.append(
                ft.Text("Todavía no hay resultados guardados.", size=12, color=MUTED)
            )
            return
        for record in saved_game_sessions[:20]:
            position = (
                f" · puesto {record['final_position']}"
                if record.get("final_position") is not None
                else ""
            )
            result_history.controls.append(
                ft.Row(
                    controls=[
                        ft.Text(
                            f"{record['event_date']} · {record['game']}",
                            color=INK,
                            expand=True,
                        ),
                        ft.Text(
                            f"{record['victories']} victorias · {record['played_minutes']:g} min{position}",
                            color=MUTED,
                            size=12,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            )

    async def save_game_result(e: ft.Event[ft.Button]) -> None:
        if supabase is None or not session:
            result_feedback.value = "Inicia sesión antes de guardar una partida."
            page.update()
            return
        try:
            game = result_game_field.value or "Racer"
            event_date = date.fromisoformat(result_date_field.value or "")
            if event_date > date.today():
                raise ValueError("La fecha no puede estar en el futuro.")
            duration_minutes = infer_event_duration_minutes(event_date, game)
            if not (result_wins_field.value or "").strip():
                raise ValueError("Indica cuántas victorias conseguiste.")
            victories = _parse_number(result_wins_field.value or "", "Victorias", integer=True)
            played_minutes = _parse_number(result_minutes_field.value or "", "Minutos jugados")
            if played_minutes <= 0 or played_minutes > duration_minutes:
                raise ValueError(f"El tiempo debe ser mayor que 0 y no superar {duration_minutes} minutos.")
            raw_position = (result_position_field.value or "").strip()
            position = (
                _parse_number(raw_position, "Puesto final", integer=True)
                if raw_position
                else None
            )
            if position is not None and not 1 <= position <= 1500:
                raise ValueError("El puesto final debe estar entre 1 y 1500.")
            record = {
                "user_id": session["user_id"],
                "game": game,
                "event_date": event_date.isoformat(),
                "victories": victories,
                "final_position": position,
                "played_minutes": played_minutes,
            }
            result_feedback.value = "Guardando resultado…"
            page.update()
            await asyncio.to_thread(
                supabase.save_game_session, record, session["access_token"]
            )
            saved_game_sessions[:] = await asyncio.to_thread(
                supabase.get_game_sessions,
                session["user_id"],
                session["access_token"],
            )
            render_game_history()
            result_feedback.value = "Resultado guardado. Si repites evento y minijuego, se actualizará ese registro."
        except (SupabaseError, ValueError) as exc:
            result_feedback.value = str(exc)
        page.update()

    save_game_result_button = ft.Button(
        "Guardar resultado",
        icon=ft.Icons.SAVE,
        disabled=True,
        on_click=save_game_result,
    )

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
            personal_pace = (
                estimate_saved_session_pace(saved_game_sessions, game_field.value or "Racer")
                if session
                else None
            )
            if personal_pace:
                for position, estimate in prediction["thresholds"].items():
                    estimate["time"] = estimate_time_minutes(
                        int(estimate["estimated_victories"]), personal_pace
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
                "Con sesión iniciada, el tiempo personal usa tus resultados guardados; si no, puedes indicar un jugador del histórico. La duración se infiere del calendario.",
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

    account_view = ft.Column(
        controls=[
            _section_heading("Mi cuenta", "Sincroniza tus datos de ingresos entre sesiones"),
            account_feedback,
            email_field,
            password_field,
            ft.Row(controls=[sign_in_button, sign_up_button], spacing=8),
            ft.Row(controls=[save_profile_button, sign_out_button], spacing=8),
            ft.Text(
                "Se sincronizan tu perfil y tus resultados personales. El histórico general de predicciones sigue siendo local.",
                size=12,
                color=MUTED,
            ),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    render_game_history()
    results_view = ft.Column(
        controls=[
            _section_heading("Resultados", "Guarda y consulta tus partidas"),
            result_feedback,
            ft.Row(controls=[result_game_field, result_date_field], spacing=10),
            ft.Row(controls=[result_wins_field, result_position_field], spacing=10),
            result_minutes_field,
            ft.Text(
                "Se permite una entrada por minijuego y fecha; si la vuelves a guardar, se actualiza.",
                size=12,
                color=MUTED,
            ),
            save_game_result_button,
            _section_heading("Mis partidas"),
            result_history,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    nav_buttons: list[ft.Button] = []
    app_views = [income_view, minigame_view, account_view, results_view]

    def select_view(index: int) -> Callable[[ft.Event[ft.Button]], None]:
        def handler(e: ft.Event[ft.Button]) -> None:
            for view_index, view in enumerate(app_views):
                view.visible = view_index == index
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
            ft.Button(
                "Mi cuenta",
                icon=ft.Icons.ACCOUNT_CIRCLE,
                style=ft.ButtonStyle(bgcolor="transparent", color=INK),
                on_click=select_view(2),
                expand=True,
            ),
            ft.Button(
                "Resultados",
                icon=ft.Icons.HISTORY,
                style=ft.ButtonStyle(bgcolor="transparent", color=INK),
                on_click=select_view(3),
                expand=True,
            ),
        ]
    )
    minigame_view.visible = False
    account_view.visible = False
    results_view.visible = False

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
                                ft.Container(expand=True)
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                    ),
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=16, vertical=8),
                        content=ft.Column(
                            controls=[
                                ft.Row(controls=nav_buttons[:2], spacing=8),
                                ft.Row(controls=nav_buttons[2:], spacing=8),
                            ],
                            spacing=4,
                        ),
                    ),
                    ft.Container(
                        content=ft.Column(controls=app_views, expand=True),
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
