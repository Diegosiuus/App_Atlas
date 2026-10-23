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
    ARCHIVED_GAMES,
    GameRecord,
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


def _admin_import_rows(raw_text: str) -> list[dict]:
    rows: list[dict] = []
    seen_positions: set[tuple[str, date, int]] = set()
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        fields = line.split()
        if not fields or fields[0].startswith("#") or fields[0].lower() == "juego":
            continue
        if len(fields) < 8:
            raise ValueError(
                f"Línea {line_number}: se esperaban 8 o 9 columnas del registro."
            )
        game, raw_date, raw_position, raw_wins, raw_pool, raw_earned = fields[:6]
        has_played_and_duration = False
        if len(fields) >= 9:
            try:
                if fields[6].upper() == "NA" or float(fields[6]) >= 0:
                    int(fields[7])
                    has_played_and_duration = True
            except ValueError:
                pass
        raw_duration = fields[7] if has_played_and_duration else fields[6]
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
        except ValueError as exc:
            raise ValueError(f"Línea {line_number}: fecha o valores numéricos inválidos.") from exc

        if event_date > date.today():
            raise ValueError(f"Línea {line_number}: no se admiten eventos futuros.")
        if not 4 <= position <= 1500 or victories < 0 or total_coins <= 0:
            raise ValueError(f"Línea {line_number}: puesto, victorias o bolsa inválidos.")
        position_key = (game, event_date, position)
        if position_key in seen_positions:
            raise ValueError(f"Línea {line_number}: posición repetida en el mismo evento.")
        seen_positions.add(position_key)
        if coins_earned is not None and coins_earned < 0:
            raise ValueError(f"Línea {line_number}: monedas ganadas no puede ser negativo.")
        if game in ARCHIVED_GAMES:
            if not is_last_saturday(event_date):
                raise ValueError(f"Línea {line_number}: Racer(V) solo corresponde al último sábado.")
            expected_duration = 60
        else:
            try:
                expected_duration = infer_event_duration_minutes(event_date, game)
            except ValueError as exc:
                raise ValueError(f"Línea {line_number}: {exc}") from exc
        if duration is None:
            duration = expected_duration
        allowed_durations = {60} if expected_duration == 60 else {120, 180}
        if duration not in allowed_durations:
            raise ValueError(f"Línea {line_number}: duración no válida para {game} el {event_date:%d/%m/%Y}.")
        rows.append(
            {
                "game": game,
                "event_date": event_date.isoformat(),
                "position": position,
                "victories": victories,
                "total_coins": total_coins,
                "coins_earned": coins_earned,
                "duration_minutes": duration,
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
    page.title = "Atlas Planner"
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

    game_field = ft.Dropdown(
        label="Minijuego",
        value="Racer",
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    prediction_event_date = _next_event_date()
    date_field = ft.Button(
        prediction_event_date.strftime("%d/%m/%Y"), icon=ft.Icons.CALENDAR_MONTH
    )
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
    training_feedback = ft.Text("Las predicciones usarán el histórico local y el compartido aprobado.", size=12, color=MUTED)
    result_game_field = ft.Dropdown(
        label="Minijuego",
        value="Racer",
        options=[ft.DropdownOption(key=game, text=game) for game in sorted(ACTIVE_GAMES)],
        expand=True,
    )
    result_event_date = _previous_event_date("Racer")
    result_date_field = ft.Button(
        result_event_date.strftime("%d/%m/%Y"), icon=ft.Icons.CALENDAR_MONTH
    )
    result_wins_field = _number_field("Victorias")
    result_position_field = _number_field("Puesto final")
    result_minutes_field = _number_field("Minutos jugados", "0")
    result_feedback = ft.Text("Inicia sesión para guardar resultados.", size=13, color=MUTED)
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
    admin_import_field = ft.TextField(
        label="Filas del registro", multiline=True, min_lines=7, max_lines=14,
        hint_text="Juego Fecha Posicion Victorias MonedasTotales MonedasGanadas TiempoJugado(min) Duracion(min) Jugador",
    )

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

    def update_selected_date(e: ft.Event[ft.DatePicker], *, for_result: bool) -> None:
        nonlocal prediction_event_date, result_event_date
        selected = e.control.value
        if selected is None:
            return
        if isinstance(selected, datetime):
            selected = selected.date()
        game = (result_game_field if for_result else game_field).value or "Racer"
        feedback = result_date_feedback if for_result else prediction_date_feedback
        try:
            if for_result and selected > date.today():
                raise ValueError("La fecha de la partida no puede estar en el futuro.")
            infer_event_duration_minutes(selected, game)
        except ValueError as exc:
            feedback.value = str(exc)
            page.update()
            return
        feedback.value = ""
        if for_result:
            result_event_date = selected
            result_date_field.content = selected.strftime("%d/%m/%Y")
        else:
            prediction_event_date = selected
            date_field.content = selected.strftime("%d/%m/%Y")
        page.update()

    prediction_date_picker.on_change = lambda e: update_selected_date(e, for_result=False)
    result_date_picker.on_change = lambda e: update_selected_date(e, for_result=True)

    def open_prediction_calendar(e: ft.Event[ft.Button]) -> None:
        prediction_date_picker.value = prediction_event_date
        page.show_dialog(prediction_date_picker)

    def open_result_calendar(e: ft.Event[ft.Button]) -> None:
        result_date_picker.value = result_event_date
        page.show_dialog(result_date_picker)

    date_field.on_click = open_prediction_calendar
    result_date_field.on_click = open_result_calendar

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
            training_feedback.value = (
                f"Histórico para predicciones: {len(records)} observaciones locales y compartidas aprobadas."
            )
            training_feedback.color = MUTED
            return True
        except SupabaseError as exc:
            records[:] = local_records
            training_feedback.value = f"No se pudo cargar el histórico compartido; se usa el local. {exc}"
            training_feedback.color = AMBER
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
        player_field.value = username
        player_field.visible = False
        save_profile_button.disabled = False
        sign_out_button.disabled = False
        save_game_result_button.disabled = False
        page.clean()
        page.add(app_root)
        if session["is_admin"] == "true":
            await reload_pending_results()

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
            account_feedback.value = "Perfil guardado en tu cuenta."
        except (SupabaseError, ValueError) as exc:
            account_feedback.value = str(exc)
        page.update()

    async def sign_out(e: ft.Event[ft.Button]) -> None:
        try:
            await clear_refresh_token(session_preferences)
        except Exception:
            account_feedback.value = "No se pudo borrar la sesión guardada. Inténtalo de nuevo."
            page.update()
            return
        session.clear()
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
        player_field.value = ""
        player_field.visible = True
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
            event_date = result_event_date
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
        if data_error and not records:
            game_feedback.controls.append(ft.Text(f"No se pudo leer el registro: {data_error}", color=RED))
            page.update()
            return
        try:
            prediction = predict_event(
                records,
                game_field.value or "Racer",
                prediction_event_date,
                player=(session.get("username") or (player_field.value or "").strip()) or None,
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
            if prediction["duration_minutes"] == 180 or pool["confidence"] == "baja":
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
            save_profile_button,
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
            player_field,
            ft.Text(
                "Con sesión iniciada, el tiempo personal usa tus resultados guardados; si no, puedes indicar un jugador del histórico. La duración se infiere del calendario.",
                size=12,
                color=MUTED,
            ),
            training_feedback,
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

    async def reload_pending_results() -> None:
        if not session or session.get("is_admin") != "true":
            return
        try:
            pending = await asyncio.to_thread(
                supabase.get_pending_results, session["access_token"]
            )
            pending_results_view.controls.clear()
            if not pending:
                pending_results_view.controls.append(
                    ft.Text("No hay resultados pendientes de revisión.", size=13, color=MUTED)
                )
            for item in pending:
                pending_results_view.controls.append(make_pending_result(item))
            admin_feedback.value = f"{len(pending)} resultados pendientes."
            admin_feedback.color = MUTED
        except SupabaseError as exc:
            admin_feedback.value = f"No se pudo consultar la cola de revisión: {exc}"
            admin_feedback.color = RED
        page.update()

    async def submit_admin_import(rows: list[dict], label: str) -> None:
        if not session or session.get("is_admin") != "true":
            return
        if len(rows) > 5000:
            admin_feedback.value = "La carga no puede superar 5000 observaciones."
            admin_feedback.color = RED
            page.update()
            return
        seen_samples: set[tuple[str, str, int]] = set()
        event_metadata: dict[tuple[str, str], tuple[int, int]] = {}
        for row in rows:
            sample_key = (row["game"], row["event_date"], int(row["position"]))
            if sample_key in seen_samples:
                admin_feedback.value = f"Hay una posición duplicada en {row['game']} el {row['event_date']}."
                admin_feedback.color = RED
                page.update()
                return
            seen_samples.add(sample_key)
            event_key = sample_key[:2]
            metadata = (int(row["duration_minutes"]), int(row["total_coins"]))
            if event_key in event_metadata and event_metadata[event_key] != metadata:
                admin_feedback.value = f"Duración o bolsa inconsistente en {row['game']} el {row['event_date']}."
                admin_feedback.color = RED
                page.update()
                return
            event_metadata[event_key] = metadata
        admin_feedback.value = f"Importando {len(rows)} observaciones..."
        admin_feedback.color = MUTED
        page.update()
        try:
            inserted = await asyncio.to_thread(
                supabase.import_observations, rows, session["access_token"]
            )
            admin_import_field.value = ""
            await refresh_training_records()
            admin_feedback.value = f"{label}: {inserted} nuevas; {len(rows) - inserted} ya existían."
            admin_feedback.color = TEAL
        except (SupabaseError, ValueError) as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
        page.update()

    async def import_pasted_observations(e: ft.Event[ft.Button]) -> None:
        try:
            await submit_admin_import(_admin_import_rows(admin_import_field.value or ""), "Importación completada")
        except ValueError as exc:
            admin_feedback.value = str(exc)
            admin_feedback.color = RED
            page.update()

    async def import_local_observations(e: ft.Event[ft.Button]) -> None:
        if not local_records:
            admin_feedback.value = "No hay un registro local disponible para importar."
            admin_feedback.color = RED
            page.update()
            return
        rows = [
            {
                "game": item.game,
                "event_date": item.event_date.isoformat(),
                "position": item.position,
                "victories": item.victories,
                "total_coins": item.total_coins,
                "coins_earned": item.coins_earned,
                "duration_minutes": item.duration_minutes,
            }
            for item in local_records
        ]
        await submit_admin_import(rows, "Histórico local anonimizado")

    async def refresh_admin(e: ft.Event[ft.Button]) -> None:
        await reload_pending_results()

    def review_action(
        item: dict,
        approve: bool,
        pool_field: ft.TextField,
        duration_field: ft.Dropdown,
        note_field: ft.TextField,
    ) -> Callable[[ft.Event[ft.Button]], object]:
        async def handler(e: ft.Event[ft.Button]) -> None:
            if not session or session.get("is_admin") != "true":
                return
            try:
                total_coins = (
                    _parse_number(pool_field.value or "", "Bolsa total", integer=True)
                    if approve
                    else None
                )
                duration = int(duration_field.value) if approve else None
                await asyncio.to_thread(
                    supabase.review_result,
                    int(item["id"]), approve, total_coins, duration,
                    (note_field.value or "").strip(), session["access_token"],
                )
                admin_feedback.value = "Resultado aprobado e incorporado al histórico." if approve else "Resultado rechazado."
                admin_feedback.color = TEAL
                if approve:
                    await refresh_training_records()
                await reload_pending_results()
            except (SupabaseError, ValueError, KeyError) as exc:
                admin_feedback.value = str(exc)
                admin_feedback.color = RED
                page.update()

        return handler

    def make_pending_result(item: dict) -> ft.Control:
        pool_field = _number_field("Bolsa total de monedas")
        event_date = date.fromisoformat(str(item["event_date"]))
        default_duration = 60 if is_last_saturday(event_date) else 180
        duration_field = ft.Dropdown(
            label="Duración del evento",
            value=str(default_duration),
            options=[ft.DropdownOption(key=str(value), text=f"{value // 60} h") for value in (60, 120, 180)],
            expand=True,
        )
        note_field = ft.TextField(label="Nota de revisión (opcional)", multiline=True, min_lines=1, max_lines=3)
        identity = item.get("username") or "Usuario"
        position = item.get("final_position")
        rank_label = f"Puesto {position}" if position is not None else "Sin puesto indicado"
        return _panel(
            ft.Column(
                controls=[
                    ft.Text(f"{event_date:%d/%m/%Y} · {item['game']} · {identity}", weight=ft.FontWeight.BOLD, color=INK),
                    ft.Text(f"{rank_label} · {item['victories']} victorias · {item['played_minutes']:g} min", size=13, color=MUTED),
                    ft.Row(controls=[pool_field, duration_field], spacing=8),
                    note_field,
                    ft.Row(
                        controls=[
                            ft.Button(
                                "Aprobar",
                                icon=ft.Icons.CHECK,
                                disabled=position is None or not 4 <= int(position) <= 1500,
                                on_click=review_action(item, True, pool_field, duration_field, note_field),
                            ),
                            ft.Button("Rechazar", icon=ft.Icons.CLOSE, on_click=review_action(item, False, pool_field, duration_field, note_field)),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
            )
        )

    admin_view = ft.Column(
        controls=[
            _section_heading("Administración", "Moderación y datos anónimos de entrenamiento"),
            admin_feedback,
            ft.Row(
                controls=[
                    _section_heading("Resultados de usuarios", "Pendientes hasta tu revisión"),
                    ft.Button("Actualizar", icon=ft.Icons.REFRESH, on_click=refresh_admin),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            pending_results_view,
            _section_heading("Importar observaciones", "Las etiquetas de jugador no se envían a Supabase"),
            ft.Text(
                "Pega filas de 8 columnas (Juego Fecha Posición Victorias MonedasTotales MonedasGanadas Duración Jugador) o 9 columnas del registro original. Se acepta AAAA-MM-DD, DD-MM-AAAA o DD/MM/AAAA.",
                size=12,
                color=MUTED,
            ),
            admin_import_field,
            ft.Row(
                controls=[
                    ft.Button("Importar filas", icon=ft.Icons.UPLOAD, on_click=import_pasted_observations),
                    ft.Button("Importar registro local anónimo", icon=ft.Icons.FOLDER_OPEN, on_click=import_local_observations),
                ],
                wrap=True,
                spacing=8,
            ),
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
            _section_heading("Bienvenido a Atlas Planner", "Inicia sesión o crea tu cuenta para continuar"),
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
            ft.Text("ATLAS PLANNER", size=13, weight=ft.FontWeight.BOLD, color=TEAL),
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
