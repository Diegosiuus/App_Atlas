"""Mobile-first PWA prototype for Atlas Earth estimates."""

from __future__ import annotations

import asyncio
import re
import sys
from datetime import date, datetime, timedelta
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
from .auth_session import clear_refresh_token, load_refresh_token, store_refresh_token
from .minigame_model import (
    ACTIVE_GAMES,
    GameRecord,
    TARGET_POSITIONS,
    NORMAL_EVENT_WEEKDAYS,
    expected_regular_game,
    infer_event_duration_minutes,
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
APP_NAME = "Atlas Earth Calc"
PRIZE_TIERS = ((4, 25), (26, 50), (51, 100), (101, 500), (501, 1500))


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


def _picker_selected_date(event_value: object, control_value: object) -> date | None:
    for value in (event_value, control_value):
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone().date()
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            if "T" in value:
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
                return parsed.astimezone().date() if parsed.tzinfo else parsed.date()
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _build_admin_event_payload(
    game: str,
    event_date: date,
    total_coins: str,
    top_award_values: dict[int, str],
    tier_values: dict[tuple[int, int], str],
) -> dict:
    expected_duration = infer_event_duration_minutes(event_date, game)
    if event_date > date.today():
        raise ValueError("La fecha del evento no puede ser futura.")
    pool = _parse_number(total_coins, "Bolsa total", integer=True)
    if pool <= 0:
        raise ValueError("La bolsa total debe ser mayor que cero.")
    tiers = []
    for position in (1, 2, 3):
        raw_value = (top_award_values.get(position) or "").strip()
        if not raw_value:
            raise ValueError(f"Completa las monedas del puesto {position}.")
        amount = _parse_number(raw_value, f"Monedas del puesto {position}", integer=True)
        tiers.append({"position_min": position, "position_max": position, "coins_awarded": amount})
    for minimum, maximum in PRIZE_TIERS:
        raw_value = (tier_values.get((minimum, maximum)) or "").strip()
        if not raw_value:
            raise ValueError(f"Completa las monedas del tramo {minimum}–{maximum}.")
        amount = _parse_number(raw_value, f"Monedas del tramo {minimum}–{maximum}", integer=True)
        tiers.append({"position_min": minimum, "position_max": maximum, "coins_awarded": amount})
    if sum(item["coins_awarded"] for item in tiers) > pool:
        raise ValueError("La suma de los premios individuales y los tramos no puede superar la bolsa total.")
    return {
        "game": game,
        "event_date": event_date.isoformat(),
        "duration_minutes": expected_duration,
        "total_coins": pool,
        "tiers": tiers,
    }


def _build_admin_observation_payload(position: str, victories: str) -> dict:
    if not victories.strip():
        raise ValueError("Indica cuántas victorias tuvo el jugador.")
    rank = _parse_number(position, "Puesto", integer=True)
    wins = _parse_number(victories, "Victorias", integer=True)
    if rank not in TARGET_POSITIONS:
        raise ValueError("Las observaciones anónimas solo admiten los puestos 25, 50, 100, 500 y 1500.")
    return {
        "position": rank,
        "victories": wins,
    }


def _build_player_position_payload(position: str) -> dict:
    if not position.strip():
        raise ValueError("Indica un puesto final igual o superior a 1.")
    rank = _parse_number(position, "Puesto final", integer=True)
    if rank < 1:
        raise ValueError("El puesto final debe ser igual o superior a 1.")
    return {"final_position": rank, "coins_earned": 0 if rank > 1500 else None}


def _approved_game_sessions(records: list[dict]) -> list[dict]:
    return [record for record in records if record.get("review_status") == "approved"]


def _load_local_records(file_path: str | Path) -> list[GameRecord]:
    if sys.platform == "emscripten":
        return []
    return load_records(file_path)


def _local_registry_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "private" / "registro_juegos.txt"


def _build_profile_payload(
    user_id: str,
    username: str,
    counts: dict[str, int],
    badge_count: int,
    normal_hours: float,
    srb_hours: float,
) -> dict:
    username = username.strip()
    if not re.fullmatch(r"[a-zA-Z0-9._-]{3,24}", username):
        raise ValueError("El nombre de usuario debe tener entre 3 y 24 caracteres válidos.")
    if normal_hours > 24:
        raise ValueError("Boost normal: el máximo es 24 horas al día.")
    if srb_hours > 64:
        raise ValueError("SRB activo: el máximo es 64 horas al mes.")
    return {
        "user_id": user_id,
        "username": username,
        "common_parcels": counts["common"],
        "rare_parcels": counts["rare"],
        "epic_parcels": counts["epic"],
        "legendary_parcels": counts["legendary"],
        "badge_count": badge_count,
        "normal_boost_hours_per_day": normal_hours,
        "srb_boost_hours_per_month": srb_hours,
    }


def _next_event_date(today: date | None = None) -> date:
    start = today or date.today()
    for offset in range(15):
        candidate = start + timedelta(days=offset)
        if candidate.weekday() in NORMAL_EVENT_WEEKDAYS:
            return candidate
    raise RuntimeError("No event date found in the next two weeks")


def _previous_event_date(today: date | None = None) -> date:
    start = today or date.today()
    for offset in range(15):
        candidate = start - timedelta(days=offset)
        try:
            expected_regular_game(candidate)
            return candidate
        except ValueError:
            continue
    raise RuntimeError("No recent event date found")


def _admin_import_rows(raw_text: str) -> list[dict]:
    rows: list[dict] = []
    seen_positions: set[tuple[str, date, int]] = set()
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        fields = line.split()
        if not fields or fields[0].startswith("#") or fields[0].lower() == "juego":
            continue
        if len(fields) < 7:
            raise ValueError(f"Línea {line_number}: se esperaban al menos 7 columnas.")
        game, raw_date, raw_position, raw_wins, raw_pool, raw_earned = fields[:6]
        raw_duration = fields[6]
        raw_losses = None
        raw_phase = None
        legacy_layout = False
        if len(fields) >= 9:
            try:
                old_duration = int(fields[7])
                legacy_layout = float(fields[6]) != 720 and old_duration in {60, 120, 180}
            except ValueError:
                pass
        if legacy_layout:
            raw_duration = fields[7]
        else:
            raw_losses = fields[7] if len(fields) >= 8 else None
            raw_phase = fields[8] if len(fields) >= 9 else None
        try:
            try:
                event_date = date.fromisoformat(raw_date)
            except ValueError:
                try:
                    event_date = datetime.strptime(raw_date, "%d-%m-%Y").date()
                except ValueError:
                    event_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
            position = int(raw_position)
            victories = int(raw_wins)
            total_coins = int(raw_pool)
            coins_earned = None if raw_earned.upper() == "NA" else int(raw_earned)
            duration = int(raw_duration) if raw_duration.upper() != "NA" else None
            losses = None if raw_losses is None or raw_losses.upper() == "NA" else int(raw_losses)
            phase = None if raw_phase is None or raw_phase.upper() == "NA" else raw_phase.lower()
        except ValueError as exc:
            raise ValueError(f"Línea {line_number}: fecha o valores numéricos inválidos.") from exc

        if event_date > date.today():
            raise ValueError(f"Línea {line_number}: no se admiten eventos futuros.")
        if position not in TARGET_POSITIONS or victories < 0 or total_coins <= 0:
            raise ValueError(f"Línea {line_number}: puesto, victorias o bolsa inválidos.")
        position_key = (game, event_date, position)
        if position_key in seen_positions:
            raise ValueError(f"Línea {line_number}: posición repetida en el mismo evento.")
        seen_positions.add(position_key)
        if coins_earned is not None and coins_earned < 0:
            raise ValueError(f"Línea {line_number}: monedas ganadas no puede ser negativo.")
        if losses is not None and not 0 <= losses <= 10:
            raise ValueError(f"Línea {line_number}: derrotas fuera del rango 0–10.")
        if phase is not None and phase not in {"inicio", "medio", "final"}:
            raise ValueError(f"Línea {line_number}: franja debe ser inicio, medio o final.")
        try:
            expected_duration = infer_event_duration_minutes(event_date, game)
        except ValueError as exc:
            raise ValueError(f"Línea {line_number}: {exc}") from exc
        if duration is None:
            duration = expected_duration
        if duration != expected_duration:
            raise ValueError(f"Línea {line_number}: los torneos regulares deben durar 12 h.")
        rows.append(
            {
                "game": game,
                "event_date": event_date.isoformat(),
                "position": position,
                "victories": victories,
                "total_coins": total_coins,
                "coins_earned": coins_earned,
                "duration_minutes": duration,
                "losses": losses,
                "activity_phase": phase,
            }
        )
    if not rows:
        raise ValueError("No se encontraron observaciones para importar.")
    return rows


def _training_records(rows: list[dict]) -> list[GameRecord]:
    records: list[GameRecord] = []
    for row in rows:
        event = row.get("event")
        if isinstance(event, list):
            event = event[0] if event else None
        if not isinstance(event, dict) or not event.get("game") or not event.get("event_date"):
            continue
        records.append(
            GameRecord(
                game=str(event["game"]),
                event_date=date.fromisoformat(str(event["event_date"])),
                position=int(row["position"]),
                victories=int(row["victories"]),
                total_coins=int(event["total_coins"]),
                coins_earned=int(row.get("coins_earned") or 0),
                played_minutes=None,
                duration_minutes=int(event["duration_minutes"]),
                player=None,
                losses=None if row.get("losses") is None else int(row["losses"]),
                activity_phase=row.get("activity_phase"),
                event_format=event.get("event_format"),
                archived=bool(event.get("archived", False)),
            )
        )
    return records


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
    page.title = APP_NAME
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=TEAL, use_material3=True)
    page.bgcolor = CANVAS
    page.padding = 0
    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[ft.Locale("es", "ES")],
        current_locale=ft.Locale("es", "ES"),
    )
    supabase = SupabaseConfig.from_environment()
    session_preferences = ft.SharedPreferences()
    session_restore_pending = supabase is not None
    session: dict[str, str] = {}
    saved_game_sessions: list[dict] = []
    data_path = _local_registry_path()
    try:
        records = _load_local_records(data_path)
        data_error = None
    except (OSError, ValueError) as exc:
        records = []
        data_error = str(exc)
    local_records = records.copy()

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

    prediction_event_date = _next_event_date()
    game_field = ft.Dropdown(
        label="Minijuego",
        value=expected_regular_game(prediction_event_date),
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    date_field = ft.Button(
        prediction_event_date.strftime("%d/%m/%Y"), icon=ft.Icons.CALENDAR_MONTH
    )
    target_field = ft.Dropdown(
        label="Puesto objetivo",
        value="25",
        options=[ft.DropdownOption(key=str(position), text=str(position)) for position in TARGET_POSITIONS],
        expand=True,
    )
    game_feedback = ft.Column(spacing=10)
    result_event_date = _previous_event_date()
    result_game_field = ft.Dropdown(
        label="Minijuego",
        value=expected_regular_game(result_event_date),
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    result_date_field = ft.Button(
        result_event_date.strftime("%d/%m/%Y"), icon=ft.Icons.CALENDAR_MONTH
    )
    result_wins_field = _number_field("Victorias")
    result_position_field = _number_field("Puesto final (1 o superior)", "")
    result_losses_field = _number_field("Derrotas (0–10)", "")
    result_phase_field = ft.Dropdown(
        label="Franja principal (opcional)",
        value=None,
        options=[
            ft.DropdownOption(key="inicio", text="Inicio (0–4 h)"),
            ft.DropdownOption(key="medio", text="Tramo medio (4–8 h)"),
            ft.DropdownOption(key="final", text="Tramo final (8–12 h)"),
        ],
        expand=True,
    )
    result_feedback = ft.Text("Inicia sesión para guardar resultados.", size=13, color=MUTED)
    profile_feedback = ft.Text(size=13, color=MUTED)
    prediction_date_feedback = ft.Text(size=12, color=RED)
    result_date_feedback = ft.Text(size=12, color=RED)
    result_history = ft.Column(spacing=6)
    username_field = ft.TextField(label="Nombre de usuario")
    email_field = ft.TextField(label="Correo electrónico", keyboard_type=ft.KeyboardType.EMAIL)
    password_field = ft.TextField(label="Contraseña", password=True, can_reveal_password=True)
    account_feedback = ft.Text(
        "Configura Supabase en el archivo .env para habilitar tu cuenta."
        if supabase is None
        else "Inicia sesión o crea una cuenta para sincronizar tu perfil.",
        size=13,
        color=AMBER if supabase is None else MUTED,
    )
    auth_feedback = ft.Text(
        "Configura Supabase en el archivo .env para habilitar el acceso."
        if supabase is None
        else "Comprobando sesión guardada…" if session_restore_pending else "",
        size=13,
        color=AMBER if supabase is None else MUTED,
    )
    sign_in_button = ft.Button(
        "Iniciar sesión", icon=ft.Icons.LOGIN,
        disabled=supabase is None or session_restore_pending,
    )
    sign_up_button = ft.Button(
        "Crear cuenta", icon=ft.Icons.PERSON_ADD,
        disabled=supabase is None or session_restore_pending,
    )
    save_profile_button = ft.Button("Guardar perfil", icon=ft.Icons.SAVE, disabled=True)
    sign_out_button = ft.Button("Cerrar sesión", icon=ft.Icons.LOGOUT, disabled=True)
    account_identity = ft.Text(size=13, color=MUTED)
    auth_view: ft.Container
    auth_root: ft.SafeArea
    app_root: ft.SafeArea
    admin_nav_button: ft.Button
    admin_feedback = ft.Text(size=12, color=MUTED)
    pending_results_view = ft.Column(spacing=10)
    known_pending_results: dict[int, tuple] | None = None
    admin_poll_future = None
    admin_event_date = _previous_event_date()
    admin_game_field = ft.Dropdown(
        label="Minijuego", value=expected_regular_game(admin_event_date),
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    admin_event_exists: bool | None = False
    admin_event_date_button = ft.Button(
        admin_event_date.strftime("%d/%m/%Y"), icon=ft.Icons.CALENDAR_MONTH
    )
    admin_total_coins_field = _number_field("Bolsa total del evento", "")
    admin_top_award_fields = {
        position: _number_field(f"Premio individual · puesto {position}", "")
        for position in (1, 2, 3)
    }
    admin_tier_fields = {
        bracket: _number_field(f"Monedas agregadas · puestos {bracket[0]}–{bracket[1]}", "")
        for bracket in PRIZE_TIERS
    }
    admin_observation_position = _number_field("Puesto límite (25, 50, 100, 500 o 1500)", "")
    admin_observation_wins = _number_field("Victorias", "")

    prediction_date_picker = ft.DatePicker(
        value=prediction_event_date,
        first_date=date(2020, 1, 1),
        last_date=date.today() + timedelta(days=365 * 5),
        locale=ft.Locale("es", "ES"),
        help_text="Elige la fecha del evento",
        cancel_text="Cancelar",
        confirm_text="Aceptar",
    )
    result_date_picker = ft.DatePicker(
        value=result_event_date,
        first_date=date(2020, 1, 1),
        last_date=date.today(),
        locale=ft.Locale("es", "ES"),
        help_text="Elige la fecha de la partida",
        cancel_text="Cancelar",
        confirm_text="Aceptar",
    )
    admin_event_date_picker = ft.DatePicker(
        value=admin_event_date,
        first_date=date(2020, 1, 1),
        last_date=date.today(),
        locale=ft.Locale("es", "ES"),
        help_text="Elige la fecha del evento",
        cancel_text="Cancelar",
        confirm_text="Aceptar",
    )

    def update_selected_date(e: ft.Event[ft.DatePicker], *, for_result: bool) -> None:
        nonlocal prediction_event_date, result_event_date
        selected = _picker_selected_date(e.data, e.control.value)
        if selected is None:
            return
        game = (result_game_field if for_result else game_field).value or "Racer"
        feedback = result_date_feedback if for_result else prediction_date_feedback
        try:
            if for_result and selected > date.today():
                raise ValueError("La fecha de la partida no puede estar en el futuro.")
            expected_game = expected_regular_game(selected)
        except ValueError as exc:
            feedback.value = str(exc)
            page.update()
            return
        feedback.value = (
            "" if game == expected_game
            else f"Esta fecha corresponde a {expected_game}, no a {game}."
        )
        feedback.color = RED if game != expected_game else MUTED
        if for_result:
            result_event_date = selected
            result_date_field.content = selected.strftime("%d/%m/%Y")
        else:
            prediction_event_date = selected
            date_field.content = selected.strftime("%d/%m/%Y")
        page.update()

    def validate_selected_game(e: ft.Event[ft.Dropdown], *, for_result: bool) -> None:
        game = (result_game_field if for_result else game_field).value or "Racer"
        event_date = result_event_date if for_result else prediction_event_date
        feedback = result_date_feedback if for_result else prediction_date_feedback
        try:
            infer_event_duration_minutes(event_date, game)
            feedback.value = ""
            feedback.color = MUTED
        except ValueError as exc:
            feedback.value = str(exc)
            feedback.color = RED
        page.update()

    prediction_date_picker.on_change = lambda e: update_selected_date(e, for_result=False)
    result_date_picker.on_change = lambda e: update_selected_date(e, for_result=True)
    game_field.on_change = lambda e: validate_selected_game(e, for_result=False)
    result_game_field.on_change = lambda e: validate_selected_game(e, for_result=True)

    async def update_admin_event_date(e: ft.Event[ft.DatePicker]) -> None:
        nonlocal admin_event_date
        selected = _picker_selected_date(e.data, e.control.value)
        if selected is None:
            return
        try:
            expected_regular_game(selected)
        except ValueError as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
        else:
            admin_event_date = selected
            admin_event_date_button.content = selected.strftime("%d/%m/%Y")
            admin_feedback.value = ""
            admin_feedback.color = MUTED
            await load_admin_event_data()
        page.update()

    admin_event_date_picker.on_change = update_admin_event_date

    async def admin_game_changed(e: ft.Event[ft.Dropdown]) -> None:
        game = admin_game_field.value or "Racer"
        try:
            infer_event_duration_minutes(admin_event_date, game)
        except ValueError as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
        else:
            admin_feedback.value = "Minijuego correcto para esta fecha."
            admin_feedback.color = MUTED
        page.update()

    admin_game_field.on_change = admin_game_changed

    def open_prediction_calendar(e: ft.Event[ft.Button]) -> None:
        prediction_date_picker.value = prediction_event_date
        page.show_dialog(prediction_date_picker)

    def open_result_calendar(e: ft.Event[ft.Button]) -> None:
        result_date_picker.value = result_event_date
        page.show_dialog(result_date_picker)

    date_field.on_click = open_prediction_calendar
    result_date_field.on_click = open_result_calendar

    def open_admin_event_calendar(e: ft.Event[ft.Button]) -> None:
        admin_event_date_picker.value = admin_event_date
        page.show_dialog(admin_event_date_picker)

    admin_event_date_button.on_click = open_admin_event_calendar

    def profile_payload(user_id: str) -> dict:
        counts = {
            rarity: _parse_number(field.value or "0", field.label or rarity, integer=True)
            for rarity, field in rarity_fields.items()
        }
        badges = _parse_number(badge_field.value or "0", "Insignias", integer=True)
        normal_hours = _parse_number(normal_hours_field.value or "0", "Boost normal")
        srb_hours = _parse_number(srb_hours_field.value or "0", "SRB activo")
        return _build_profile_payload(
            user_id,
            session.get("username", ""),
            counts,
            badges,
            normal_hours,
            srb_hours,
        )

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

    async def refresh_training_records() -> bool:
        try:
            rows = await asyncio.to_thread(
                supabase.get_training_records, session["access_token"]
            )
            combined = [*local_records, *_training_records(rows)]
            unique_records: list[GameRecord] = []
            seen: set[tuple] = set()
            for record in combined:
                key = (
                    record.game, record.event_date, record.position, record.victories,
                    record.total_coins, record.duration_minutes,
                )
                if key not in seen:
                    unique_records.append(record)
                    seen.add(key)
            records[:] = unique_records
            return True
        except SupabaseError as exc:
            records[:] = local_records
            return False

    async def establish_session(response: dict, email_hint: str = "") -> None:
        access_token = response.get("access_token")
        refresh_token = response.get("refresh_token")
        user = response.get("user") or {}
        user_id = user.get("id")
        if not access_token or not user_id:
            raise SupabaseError("Supabase no devolvió una sesión válida.")

        email = str(user.get("email") or email_hint).strip().lower()
        session.update(
            access_token=access_token,
            user_id=user_id,
            email=email,
        )
        persistence_warning = False
        if refresh_token:
            session["refresh_token"] = refresh_token
            try:
                await store_refresh_token(session_preferences, refresh_token)
            except Exception:
                persistence_warning = True

        try:
            session["is_admin"] = str(
                await asyncio.to_thread(supabase.is_admin, access_token)
            ).lower()
        except SupabaseError as exc:
            session["is_admin"] = "false"
            admin_feedback.value = f"No se pudo comprobar el rol de administrador: {exc}"
        admin_nav_button.visible = session["is_admin"] == "true"
        await refresh_training_records()

        profile = await asyncio.to_thread(supabase.get_profile, user_id, access_token)
        metadata = user.get("user_metadata") or {}
        username = (
            str(metadata.get("username") or "").strip()
            or str((profile or {}).get("username") or "").strip()
            or f"usuario_{str(user_id).replace('-', '')[:8]}"
        )
        session["username"] = username
        fill_profile(profile)
        if profile is None:
            saved_profile = await asyncio.to_thread(
                supabase.save_profile, profile_payload(user_id), access_token
            )
            fill_profile(saved_profile)

        try:
            saved_game_sessions[:] = await asyncio.to_thread(
                supabase.get_game_sessions, user_id, access_token
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

        auth_feedback.value = "Acceso correcto."
        account_feedback.value = (
            "Sesión iniciada; no se pudo recordar al recargar."
            if persistence_warning or not refresh_token
            else "Perfil y resultados sincronizados."
        )
        account_identity.value = f"{username} · {email}"
        save_profile_button.disabled = False
        sign_out_button.disabled = False
        save_game_result_button.disabled = False
        page.clean()
        page.add(app_root)
        if session["is_admin"] == "true":
            await load_admin_event_data()
            await reload_pending_results(notify_new=True)
            start_admin_notifications()

    async def authenticate(create_account: bool) -> None:
        if supabase is None:
            return
        email = (email_field.value or "").strip().lower()
        password = password_field.value or ""
        username = (username_field.value or "").strip()
        if create_account and not username:
            auth_feedback.value = "Elige un nombre de usuario."
            page.update()
            return
        if not email or not password:
            auth_feedback.value = "Introduce tu correo y contraseña."
            page.update()
            return
        auth_feedback.value = "Conectando…"
        page.update()
        try:
            if create_account:
                response = await asyncio.to_thread(supabase.sign_up, email, password, username)
            else:
                response = await asyncio.to_thread(supabase.sign_in, email, password)
            if not response.get("access_token"):
                auth_feedback.value = "Cuenta creada. Revisa tu correo y confirma la dirección antes de iniciar sesión."
                page.update()
                return
            await establish_session(response, email)
        except (SupabaseError, ValueError) as exc:
            session.clear()
            auth_feedback.value = str(exc)
        page.update()

    async def restore_session() -> None:
        nonlocal session_restore_pending
        try:
            refresh_token = await load_refresh_token(session_preferences)
            if not refresh_token or supabase is None:
                return
            response = await asyncio.to_thread(supabase.refresh_session, refresh_token)
            if not response.get("access_token"):
                await clear_refresh_token(session_preferences)
                return
            await establish_session(response)
        except SupabaseError as exc:
            session.clear()
            if exc.status_code == 400:
                await clear_refresh_token(session_preferences)
                auth_feedback.value = "La sesión guardada ha caducado. Inicia sesión de nuevo."
            else:
                auth_feedback.value = "No se pudo comprobar la sesión guardada. Intenta iniciar sesión."
        except Exception:
            session.clear()
            auth_feedback.value = "No se pudo recuperar la sesión guardada. Inicia sesión de nuevo."
        finally:
            session_restore_pending = False
            for button in auth_entry_buttons:
                button.disabled = supabase is None
            sign_in_button.disabled = supabase is None
            sign_up_button.disabled = supabase is None
            if not session and auth_feedback.value == "Comprobando sesión guardada…":
                auth_feedback.value = ""
            page.update()

    async def save_profile(e: ft.Event[ft.Button]) -> None:
        if supabase is None or not session:
            return
        try:
            profile = profile_payload(session["user_id"])
            account_feedback.value = "Guardando perfil…"
            page.update()
            saved_profile = await asyncio.to_thread(
                supabase.save_profile, profile, session["access_token"]
            )
            fill_profile(saved_profile)
            profile_feedback.value = "Perfil guardado correctamente."
            profile_feedback.color = TEAL
            account_feedback.value = "Perfil guardado correctamente."
        except (SupabaseError, ValueError) as exc:
            profile_feedback.value = str(exc)
            profile_feedback.color = RED
        page.update()

    async def sign_out(e: ft.Event[ft.Button]) -> None:
        nonlocal known_pending_results
        try:
            await clear_refresh_token(session_preferences)
        except Exception:
            account_feedback.value = "No se pudo borrar la sesión guardada. Inténtalo de nuevo."
            page.update()
            return
        session.clear()
        known_pending_results = None
        saved_game_sessions.clear()
        render_game_history()
        email_field.disabled = False
        password_field.disabled = False
        save_profile_button.disabled = True
        sign_out_button.disabled = True
        save_game_result_button.disabled = True
        result_feedback.value = "Inicia sesión para guardar resultados."
        account_feedback.value = "Sesión cerrada en este dispositivo."
        email_field.value = ""
        password_field.value = ""
        admin_nav_button.visible = False
        admin_view.visible = False
        auth_landing.visible = True
        auth_form.controls.clear()
        auth_feedback.value = "Sesión cerrada."
        page.clean()
        page.add(auth_root)
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
        visible_sessions = _approved_game_sessions(saved_game_sessions)
        if not visible_sessions:
            result_history.controls.append(
                ft.Text("Aún no hay partidas aprobadas para mostrar.", size=12, color=MUTED)
            )
            return
        for record in visible_sessions[:20]:
            position = (
                f" · puesto {record['final_position']}"
                if record.get("final_position") is not None
                else ""
            )
            archived_note = " · histórico archivado" if record.get("review_status") == "archived" else ""
            coins_earned = record.get("coins_earned")
            coins_note = f" · {coins_earned} monedas" if coins_earned is not None else ""
            result_history.controls.append(
                ft.Row(
                    controls=[
                        ft.Text(
                            f"{record['event_date']} · {record['game']}",
                            color=INK,
                            expand=True,
                        ),
                        ft.Text(
                            f"{record['victories']} victorias · "
                            f"{record.get('losses') if record.get('losses') is not None else 'derrotas N/D'} "
                            f"derrotas{position}{coins_note}{archived_note}",
                            color=MUTED,
                            size=12,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            )

    async def refresh_game_history() -> None:
        if not session or supabase is None:
            render_game_history()
            return
        try:
            saved_game_sessions[:] = await asyncio.to_thread(
                supabase.get_game_sessions,
                session["user_id"],
                session["access_token"],
            )
        except SupabaseError as exc:
            result_feedback.value = f"No se pudo actualizar el historial: {exc}"
            result_feedback.color = RED
        render_game_history()
        page.update()

    async def save_game_result(e: ft.Event[ft.Button]) -> None:
        if supabase is None or not session:
            result_feedback.value = "Inicia sesión antes de guardar una partida."
            page.update()
            return
        try:
            game = result_game_field.value or "Racer"
            event_date = result_event_date
            if event_date > date.today():
                raise ValueError("La fecha no puede estar en el futuro.")
            duration_minutes = infer_event_duration_minutes(event_date, game)
            if not (result_wins_field.value or "").strip():
                raise ValueError("Indica cuántas victorias conseguiste.")
            victories = _parse_number(result_wins_field.value or "", "Victorias", integer=True)
            raw_losses = (result_losses_field.value or "").strip()
            losses = _parse_number(raw_losses, "Derrotas", integer=True) if raw_losses else None
            if losses is not None and not 0 <= losses <= 10:
                raise ValueError("Las derrotas deben estar entre 0 y 10.")
            position_payload = _build_player_position_payload(result_position_field.value or "")
            position = position_payload["final_position"]
            record = {
                "user_id": session["user_id"],
                "game": game,
                "event_date": event_date.isoformat(),
                "victories": victories,
                **position_payload,
                "losses": losses,
                "activity_phase": result_phase_field.value or None,
            }
            existed = await asyncio.to_thread(
                supabase.game_session_exists,
                session["user_id"],
                game,
                event_date.isoformat(),
                session["access_token"],
            )
            result_feedback.value = "Guardando resultado…"
            page.update()
            await asyncio.to_thread(
                supabase.save_game_session, record, session["access_token"]
            )
            result_wins_field.value = ""
            result_position_field.value = ""
            result_losses_field.value = ""
            result_phase_field.value = None
            result_feedback.value = (
                "Resultado actualizado y enviado para revisión."
                if existed
                else "Resultado enviado para revisión. Aparecerá en Mis partidas cuando se apruebe."
            )
            result_feedback.color = TEAL
            try:
                saved_game_sessions[:] = await asyncio.to_thread(
                    supabase.get_game_sessions,
                    session["user_id"],
                    session["access_token"],
                )
                render_game_history()
            except SupabaseError as exc:
                result_feedback.value += f" No se pudo actualizar el historial: {exc}"
        except (SupabaseError, ValueError) as exc:
            result_feedback.value = str(exc)
            result_feedback.color = RED
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
        if data_error and not records:
            game_feedback.controls.append(ft.Text(f"No se pudo leer el registro: {data_error}", color=RED))
            page.update()
            return
        try:
            prediction = predict_event(
                records,
                game_field.value or "Racer",
                prediction_event_date,
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
                                color=AMBER if pool["confidence"] == "baja" else TEAL,
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
            if pool["confidence"] == "baja":
                game_feedback.controls.append(
                    ft.Text(
                        "Estimación orientativa: hay pocos torneos regulares comparables de 12 h.",
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
            save_profile_button,
            profile_feedback,
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
            ft.Row(controls=[ft.Text("Fecha del evento", expand=True), date_field]),
            prediction_date_feedback,
            ft.Button("Calcular predicción", icon=ft.Icons.INSIGHTS, on_click=game_result),
            game_feedback,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    account_view = ft.Column(
        controls=[
            _section_heading("Mi cuenta", "Tu perfil personal"),
            account_identity,
            account_feedback,
            sign_out_button,
            ft.Text(
                "Tu perfil y tus partidas son privados; las observaciones aprobadas se comparten sin identificar al jugador.",
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
            ft.Row(
                controls=[
                    result_game_field,
                    ft.Column(controls=[ft.Text("Fecha", size=12, color=MUTED), result_date_field], spacing=2),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.END,
            ),
            result_date_feedback,
            ft.Row(controls=[result_wins_field, result_position_field], spacing=10),
            ft.Row(controls=[result_losses_field, result_phase_field], spacing=10),
            save_game_result_button,
            _section_heading("Mis partidas"),
            result_history,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    async def reload_pending_results(
        *, notify_new: bool = False, render_view: bool = True
    ) -> None:
        nonlocal known_pending_results
        if not session or session.get("is_admin") != "true":
            return
        try:
            pending = await asyncio.to_thread(
                supabase.get_pending_results, session["access_token"]
            )
            current = {
                int(item["id"]): tuple(
                    item.get(key)
                    for key in ("game", "event_date", "victories", "final_position", "losses", "activity_phase")
                )
                for item in pending
            }
            first_check = known_pending_results is None
            new_count = (
                len(current)
                if known_pending_results is None
                else sum(
                    1 for result_id, signature in current.items()
                    if known_pending_results.get(result_id) != signature
                )
            )
            known_pending_results = current
            current_ids = set(current)
            admin_nav_button.content = f"Admin ({len(current_ids)})" if current_ids else "Admin"
            if render_view:
                pending_results_view.controls.clear()
                if not pending:
                    pending_results_view.controls.append(
                        ft.Text("No hay resultados pendientes de revisión.", size=13, color=MUTED)
                    )
                grouped: dict[tuple[str, str], list[dict]] = {}
                for item in pending:
                    grouped.setdefault((str(item["event_date"]), str(item["game"])), []).append(item)
                for (event_date, game), items in grouped.items():
                    parsed_date = date.fromisoformat(event_date)
                    pending_results_view.controls.append(
                        _section_heading(f"{game} · {parsed_date:%d/%m/%Y}")
                    )
                    pending_results_view.controls.extend(make_pending_result(item) for item in items)
            admin_feedback.value = f"{len(pending)} resultados pendientes."
            admin_feedback.color = MUTED
            if notify_new and new_count:
                message = f"Tienes {new_count} resultados pendientes de revisar." if first_check else (
                    f"Han llegado {new_count} resultados nuevos para revisar."
                )
                page.show_dialog(
                    ft.SnackBar(ft.Text(message), duration=ft.Duration(seconds=6))
                )
        except SupabaseError as exc:
            admin_feedback.value = f"No se pudo consultar la cola de revisión: {exc}"
            admin_feedback.color = RED
        page.update()

    async def admin_notification_loop() -> None:
        nonlocal admin_poll_future
        try:
            while session.get("is_admin") == "true":
                await asyncio.sleep(45)
                if session.get("is_admin") == "true":
                    await reload_pending_results(notify_new=True, render_view=False)
        finally:
            admin_poll_future = None

    def start_admin_notifications() -> None:
        nonlocal admin_poll_future
        if admin_poll_future is None or admin_poll_future.done():
            admin_poll_future = page.run_task(admin_notification_loop)

    async def load_admin_event_data() -> None:
        nonlocal admin_event_exists
        if not session or session.get("is_admin") != "true":
            return
        admin_event_exists = False
        for field in (
            admin_total_coins_field,
            *admin_top_award_fields.values(),
            *admin_tier_fields.values(),
        ):
            field.value = ""
        try:
            event = await asyncio.to_thread(
                supabase.get_admin_event_data,
                admin_event_date.isoformat(),
                session["access_token"],
            )
            if event and event.get("event_format") == "regular_tournament" and not event.get("archived"):
                admin_event_exists = True
                admin_game_field.value = event["game"]
                admin_total_coins_field.value = str(event["total_coins"])
                loaded_top_positions: set[int] = set()
                for tier in event.get("tiers", []):
                    bracket = (int(tier["position_min"]), int(tier["position_max"]))
                    if bracket[0] == bracket[1] and bracket[0] in admin_top_award_fields:
                        admin_top_award_fields[bracket[0]].value = str(tier["coins_awarded"])
                        loaded_top_positions.add(bracket[0])
                    elif bracket in admin_tier_fields:
                        admin_tier_fields[bracket].value = str(tier["coins_awarded"])
                missing_top_positions = set(admin_top_award_fields) - loaded_top_positions
                admin_feedback.value = (
                    "Evento existente cargado; completa los premios que faltan del top 1–3."
                    if missing_top_positions
                    else "Datos existentes del evento cargados."
                )
            else:
                admin_game_field.value = expected_regular_game(admin_event_date)
                admin_feedback.value = "Evento nuevo."
            admin_feedback.color = MUTED
        except (SupabaseError, KeyError, TypeError, ValueError) as exc:
            admin_event_exists = None
            admin_feedback.value = f"No se pudieron cargar los datos del evento: {exc}"
            admin_feedback.color = RED
        page.update()

    async def save_admin_event(e: ft.Event[ft.Button]) -> None:
        nonlocal admin_event_exists
        if not session or session.get("is_admin") != "true":
            return
        try:
            event_previously_exists = admin_event_exists
            payload = _build_admin_event_payload(
                admin_game_field.value or "Racer",
                admin_event_date,
                admin_total_coins_field.value or "",
                {position: field.value or "" for position, field in admin_top_award_fields.items()},
                {bracket: field.value or "" for bracket, field in admin_tier_fields.items()},
            )
            await asyncio.to_thread(
                supabase.save_admin_event_data, payload, session["access_token"]
            )
            admin_event_exists = True
            if event_previously_exists is None:
                admin_feedback.value = "Evento guardado o actualizado correctamente."
            else:
                admin_feedback.value = "Evento actualizado correctamente." if event_previously_exists else "Evento guardado correctamente."
            admin_feedback.color = TEAL
            await reload_pending_results()
        except (SupabaseError, ValueError) as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
        page.update()

    async def add_admin_observation(e: ft.Event[ft.Button]) -> None:
        if not session or session.get("is_admin") != "true":
            return
        try:
            game = admin_game_field.value or "Racer"
            infer_event_duration_minutes(admin_event_date, game)
            observation = _build_admin_observation_payload(
                admin_observation_position.value or "",
                admin_observation_wins.value or "",
            )
            await asyncio.to_thread(
                supabase.add_admin_observation,
                game,
                admin_event_date.isoformat(),
                observation,
                session["access_token"],
            )
            for field in (admin_observation_position, admin_observation_wins):
                field.value = ""
            await refresh_training_records()
            admin_feedback.value = "Observación guardada correctamente."
            admin_feedback.color = TEAL
        except (SupabaseError, ValueError) as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
        page.update()

    async def refresh_admin(e: ft.Event[ft.Button]) -> None:
        await reload_pending_results()

    def review_action(item: dict, approve: bool, note_field: ft.TextField) -> Callable[[ft.Event[ft.Button]], object]:
        async def handler(e: ft.Event[ft.Button]) -> None:
            if not session or session.get("is_admin") != "true":
                return
            try:
                total_coins = int(item["total_coins"]) if approve and item.get("total_coins") is not None else None
                duration = 720 if approve else None
                await asyncio.to_thread(
                    supabase.review_result,
                    int(item["id"]), approve, total_coins, duration,
                    (note_field.value or "").strip(), session["access_token"],
                )
                admin_feedback.value = "Resultado aprobado." if approve else "Resultado rechazado."
                admin_feedback.color = TEAL
                if approve:
                    await refresh_training_records()
                await reload_pending_results()
                await refresh_game_history()
            except (SupabaseError, ValueError, KeyError) as exc:
                admin_feedback.value = str(exc)
                admin_feedback.color = RED
                page.update()

        return handler

    def make_pending_result(item: dict) -> ft.Control:
        note_field = ft.TextField(label="Nota de revisión (opcional)", multiline=True, min_lines=1, max_lines=3)
        identity = item.get("username") or "Usuario"
        position = item.get("final_position")
        rank_label = f"Puesto {position}" if position is not None else "Sin puesto indicado"
        return _panel(
            ft.Column(
                controls=[
                    ft.Text(identity, weight=ft.FontWeight.BOLD, color=INK),
                    ft.Text(f"{rank_label} · {item['victories']} victorias · "
                            f"{item.get('losses') if item.get('losses') is not None else 'derrotas N/D'} derrotas", size=13, color=MUTED),
                    ft.Text(
                        (
                            "Se guardará en el historial personal; no se usa en predicciones."
                            if position is not None and (int(position) < 4 or int(position) > 1500)
                            else "Se usará en predicciones al aprobarlo."
                            if item.get("event_ready")
                            else "Se puede aprobar ahora; se incorporará al modelo cuando el evento tenga bolsa y premios completos."
                        ),
                        size=12,
                        color=TEAL if item.get("event_ready") else AMBER,
                    ),
                    note_field,
                    ft.Row(
                        controls=[
                            ft.Button(
                                "Aprobar",
                                icon=ft.Icons.CHECK,
                                disabled=(position is None or int(position) < 1),
                                on_click=review_action(item, True, note_field),
                            ),
                            ft.Button(
                                "Rechazar",
                                icon=ft.Icons.CLOSE,
                                style=ft.ButtonStyle(bgcolor="#FCE8E8", color=RED),
                                on_click=review_action(item, False, note_field),
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            )
        )

    admin_view = ft.Column(
        controls=[
            _section_heading("Administración"),
            admin_feedback,
            _section_heading("Datos del evento", "La asignación por tramo es el total de monedas de todos los jugadores de ese rango."),
            ft.Row(controls=[admin_game_field, ft.Column(controls=[ft.Text("Fecha del evento", size=12, color=MUTED), admin_event_date_button], spacing=2)], spacing=10, vertical_alignment=ft.CrossAxisAlignment.END),
            admin_total_coins_field,
            _section_heading("Premios individuales · Top 1–3"),
            *[admin_top_award_fields[position] for position in (1, 2, 3)],
            _section_heading("Premios agregados por tramo"),
            *[admin_tier_fields[bracket] for bracket in PRIZE_TIERS],
            ft.Button("Guardar evento", icon=ft.Icons.SAVE, on_click=save_admin_event),
            _section_heading("Añadir observación anónima"),
            ft.Row(controls=[admin_observation_position, admin_observation_wins], spacing=10),
            ft.Button("Añadir observación", icon=ft.Icons.ADD, on_click=add_admin_observation),
            ft.Row(
                controls=[
                    _section_heading("Resultados de usuarios"),
                    ft.Button("Actualizar", icon=ft.Icons.REFRESH, on_click=refresh_admin),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            pending_results_view,
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    nav_buttons: list[ft.Button] = []
    app_views = [income_view, minigame_view, account_view, results_view, admin_view]

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
            if index == 3 and session:
                page.run_task(refresh_game_history)
            if index == 4 and session.get("is_admin") == "true":
                page.run_task(reload_pending_results)

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
            ft.Button(
                "Admin",
                icon=ft.Icons.ADMIN_PANEL_SETTINGS,
                style=ft.ButtonStyle(bgcolor="transparent", color=INK),
                on_click=select_view(4),
                expand=True,
                visible=False,
            ),
        ]
    )
    admin_nav_button = nav_buttons[4]
    minigame_view.visible = False
    account_view.visible = False
    results_view.visible = False
    admin_view.visible = False

    login_landing_button = ft.Button(
        "Iniciar sesión", icon=ft.Icons.LOGIN,
        on_click=lambda e: show_auth_form("login"), expand=True,
        disabled=supabase is None or session_restore_pending,
    )
    register_landing_button = ft.Button(
        "Crear cuenta", icon=ft.Icons.PERSON_ADD,
        on_click=lambda e: show_auth_form("register"), expand=True,
        disabled=supabase is None or session_restore_pending,
    )
    auth_entry_buttons = [login_landing_button, register_landing_button]
    auth_landing = ft.Column(
        controls=[
            _section_heading(f"Bienvenido a {APP_NAME}", "Inicia sesión o crea tu cuenta para continuar"),
            ft.Row(
                controls=[
                    login_landing_button,
                    register_landing_button,
                ],
                spacing=10,
            ),
        ],
        spacing=18,
    )
    auth_form = ft.Column(spacing=12)

    def show_auth_form(mode: str | None) -> None:
        auth_landing.visible = mode is None
        auth_form.controls = []
        if mode:
            auth_form.controls.extend([_section_heading("Crear cuenta" if mode == "register" else "Iniciar sesión")])
            if mode == "register":
                auth_form.controls.append(username_field)
            auth_form.controls.extend([email_field, password_field])
            auth_form.controls.append(sign_up_button if mode == "register" else sign_in_button)
            auth_form.controls.append(ft.TextButton("Volver", on_click=lambda e: show_auth_form(None)))
        sign_in_button.disabled = supabase is None or session_restore_pending
        sign_up_button.disabled = supabase is None or session_restore_pending
        auth_feedback.value = (
            "Configura Supabase en el archivo .env para habilitar el acceso."
            if supabase is None
            else "Comprobando sesión guardada…" if session_restore_pending else ""
        )
        page.update()

    auth_panel = ft.Container(
        content=ft.Column(
            controls=[auth_landing, auth_form, auth_feedback],
            spacing=18,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        width=min(420, max(0, (page.width or 452) - 32)),
    )
    auth_layout = ft.Column(
        controls=[
            ft.Text(APP_NAME.upper(), size=13, weight=ft.FontWeight.BOLD, color=TEAL),
            auth_panel,
        ],
        spacing=18,
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
    auth_block = ft.Container(
        width=min(420, max(0, (page.width or 452) - 32)),
        height=max(0, (page.height or 700) - 32),
        content=auth_layout,
    )
    auth_view = ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=auth_block,
    )

    def resize_auth_view(e: ft.ControlEvent) -> None:
        available_width = min(420, max(0, (page.width or 452) - 32))
        auth_block.width = available_width
        auth_panel.width = available_width
        available_height = max(0, (page.height or 700) - 32)
        auth_block.height = available_height
        auth_layout.alignment = (
            ft.MainAxisAlignment.CENTER if available_height >= 520 else ft.MainAxisAlignment.START
        )
        page.update()

    page.on_resize = resize_auth_view

    app_shell = ft.Container(
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
                                        ft.Text(APP_NAME.upper(), size=12, weight=ft.FontWeight.BOLD, color=TEAL),
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
                                ft.Row(controls=nav_buttons[2:4], spacing=8),
                                ft.Row(controls=nav_buttons[4:], spacing=8),
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
    auth_root = ft.SafeArea(expand=True, content=auth_view)
    app_root = ft.SafeArea(expand=True, content=app_shell)
    page.add(
        auth_root
    )
    if supabase is not None:
        page.run_task(restore_session)


def _metric_row(label: str, value: str) -> ft.Control:
    return ft.Row(
        controls=[
            ft.Text(label, color=MUTED, expand=True),
            ft.Text(value, color=INK, weight=ft.FontWeight.W_500),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
    )


if __name__ == "__main__":
    ft.run(main)
