"""Minimal Supabase Auth and profile REST client for the Flet app."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is unavailable in the Pyodide browser runtime.
    def load_dotenv(*args, **kwargs) -> bool:
        return False


PROFILE_COLUMNS = (
    "user_id,username,common_parcels,rare_parcels,epic_parcels,legendary_parcels,"
    "badge_count,normal_boost_hours_per_day,srb_boost_hours_per_month"
)
GAME_SESSION_COLUMNS = (
    "id,game,event_date,victories,final_position,losses,activity_phase,review_status,created_at"
)


class SupabaseError(RuntimeError):
    """A user-safe error returned by Supabase or the network layer."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    publishable_key: str
    email_redirect_url: str | None = None

    @classmethod
    def from_environment(cls) -> SupabaseConfig | None:
        project_root = Path(__file__).resolve().parents[2]

        # Desarrollo local: intentar cargar .env
        load_dotenv(project_root / ".env")

        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        redirect_url = os.getenv("SUPABASE_EMAIL_REDIRECT_URL", "").strip()

        # Producción/PWA: si no hay .env, usar configuración pública
        if not url or not key:
            try:
                from .config_public import (
                    SUPABASE_URL,
                    SUPABASE_PUBLISHABLE_KEY,
                )

                url = SUPABASE_URL.strip().rstrip("/")
                key = SUPABASE_PUBLISHABLE_KEY.strip()
            except ImportError:
                return None

        if not url or not key or "your-project" in url or "replace_me" in key:
            return None

        return cls(
            url=url,
            publishable_key=key,
            email_redirect_url=redirect_url or None,
        )

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        access_token: str | None = None,
        prefer: str | None = None,
    ) -> object:
        headers = {
            "apikey": self.publishable_key,
            "Authorization": f"Bearer {access_token or self.publishable_key}",
            "Accept": "application/json",
        }

        if prefer:
            headers["Prefer"] = prefer

        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload)

        url = f"{self.url}{path}"

        # Flet Web/Pyodide: use the browser's fetch API.
        if sys.platform == "emscripten":
            try:
                import asyncio
                from js import fetch
                from pyodide.ffi import to_js

                # Añadir la clave pública a la URL como alternativa
                # a la cabecera apikey en el navegador.
                web_url = url
                separator = "&" if "?" in web_url else "?"
                web_url = (
                    f"{web_url}{separator}"
                    f"apikey={quote(self.publishable_key, safe='')}"
                )

                async def browser_request():
                    # Preparar las opciones de fetch como un objeto JS.
                    options = {
                        "method": method,
                        "headers": {
                            key: value
                            for key, value in headers.items()
                        },
                    }

                    if body is not None:
                        options["body"] = body

                    response = await fetch(
                        web_url,
                        to_js(options),
                    )

                    raw_text = await response.text()

                    return response.status, raw_text

                status, raw_text = asyncio.run(browser_request())

                # Gestionar errores HTTP devueltos por Supabase.
                if status >= 400:
                    try:
                        detail = json.loads(raw_text) if raw_text else {}
                    except (ValueError, TypeError):
                        detail = {}

                    message = (
                        detail.get("msg")
                        or detail.get("message")
                        or detail.get("error_description")
                    )

                    if status == 401:
                        raise SupabaseError(
                            f"Supabase respondió HTTP 401: "
                            f"{message or raw_text or 'Sin detalles'}",
                            status_code=status,
                        )

                    if status == 429:
                        raise SupabaseError(
                            "Demasiados intentos. Espera un momento "
                            "y vuelve a probar.",
                            status_code=status,
                        )

                    raise SupabaseError(
                        f"Supabase respondió HTTP {status}: "
                        f"{raw_text or 'Respuesta sin cuerpo'}",
                        status_code=status,
                    )

                # Si la respuesta no contiene datos, devolver None.
                if not raw_text:
                    return None

                # Interpretar la respuesta JSON.
                try:
                    return json.loads(raw_text)
                except (ValueError, TypeError) as exc:
                    raise SupabaseError(
                        "Supabase devolvió una respuesta inesperada."
                    ) from exc

            except SupabaseError:
                raise

            except Exception as exc:
                raise SupabaseError(
                    f"Error de conexión con Supabase: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        # Python normal (Windows/Desktop).
        request = Request(
            url,
            data=body.encode("utf-8") if body is not None else None,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = {}

            message = (
                detail.get("msg")
                or detail.get("message")
                or detail.get("error_description")
            )

            if exc.code == 401:
                raise SupabaseError(
                    "La sesión ha caducado. Inicia sesión de nuevo.",
                    status_code=exc.code,
                ) from exc

            if exc.code == 429:
                raise SupabaseError(
                    "Demasiados intentos. Espera un momento y vuelve a probar.",
                    status_code=exc.code,
                ) from exc

            raise SupabaseError(
                str(message or "Supabase rechazó la operación."),
                status_code=exc.code,
            ) from exc

        except (TimeoutError, URLError) as exc:
            raise SupabaseError(
                f"Error de conexión con Supabase: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if not raw:
            return None

        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SupabaseError(
                "Supabase devolvió una respuesta inesperada."
            ) from exc
    def sign_up(self, email: str, password: str, username: str) -> dict:
        path = "/auth/v1/signup"
        if self.email_redirect_url:
            path += "?" + urlencode({"redirect_to": self.email_redirect_url})
        return self._request(
            path,
            method="POST",
            payload={"email": email, "password": password, "data": {"username": username}},
        )

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "/auth/v1/token?grant_type=password",
            method="POST",
            payload={"email": email, "password": password},
        )

    def refresh_session(self, refresh_token: str) -> dict:
        return self._request(
            "/auth/v1/token?grant_type=refresh_token",
            method="POST",
            payload={"refresh_token": refresh_token},
        )

    def get_profile(self, user_id: str, access_token: str) -> dict | None:
        query = quote(user_id, safe="")
        result = self._request(
            f"/rest/v1/profiles?select={PROFILE_COLUMNS}&user_id=eq.{query}",
            access_token=access_token,
        )
        return result[0] if result else None

    def save_profile(self, profile: dict, access_token: str) -> dict:
        result = self._request(
            "/rest/v1/profiles",
            method="POST",
            payload=profile,
            access_token=access_token,
            prefer="resolution=merge-duplicates,return=representation",
        )
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise SupabaseError("Supabase no confirmó el guardado del perfil.")
        return result[0]

    def get_game_sessions(self, user_id: str, access_token: str) -> list[dict]:
        query = quote(user_id, safe="")
        return self._request(
            f"/rest/v1/player_game_sessions?select={GAME_SESSION_COLUMNS}"
            f"&user_id=eq.{query}&order=event_date.desc,created_at.desc&limit=100",
            access_token=access_token,
        )

    def save_game_session(self, game_session: dict, access_token: str) -> None:
        self._request(
            "/rest/v1/player_game_sessions?on_conflict=user_id,game,event_date",
            method="POST",
            payload=game_session,
            access_token=access_token,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def get_training_records(self, access_token: str) -> list[dict]:
        select = "select=position,victories,coins_earned,losses,activity_phase,event:minigame_events(game,event_date,duration_minutes,total_coins,archived,event_format)"
        rows: list[dict] = []
        offset = 0
        while True:
            page = self._request(
                f"/rest/v1/minigame_observations?{select}&order=id.asc&limit=1000&offset={offset}",
                access_token=access_token,
            )
            if not isinstance(page, list):
                raise SupabaseError("Supabase devolvió un histórico de entrenamiento inesperado.")
            rows.extend(page)
            if len(page) < 1000:
                return rows
            offset += len(page)

    def is_admin(self, access_token: str) -> bool:
        result = self._request(
            "/rest/v1/rpc/is_app_admin", method="POST", payload={}, access_token=access_token
        )
        return bool(result)

    def get_pending_results(self, access_token: str) -> list[dict]:
        return self._request(
            "/rest/v1/rpc/admin_pending_game_results", method="POST", payload={}, access_token=access_token
        )

    def review_result(self, session_id: int, approve: bool, total_coins: int | None, duration_minutes: int | None, note: str, access_token: str) -> None:
        self._request(
            "/rest/v1/rpc/admin_review_game_result", method="POST",
            payload={"p_session_id": session_id, "p_approve": approve, "p_total_coins": total_coins,
                     "p_duration_minutes": duration_minutes, "p_note": note},
            access_token=access_token,
        )

    def import_observations(self, rows: list[dict], access_token: str) -> int:
        imported = self._request(
            "/rest/v1/rpc/admin_import_model_rows", method="POST",
            payload={"p_rows": rows}, access_token=access_token,
        )
        if not isinstance(imported, int):
            raise SupabaseError("Supabase devolvió un recuento de importación inesperado.")
        return imported
