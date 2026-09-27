"""Minimal Supabase Auth and profile REST client for the Flet app."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


PROFILE_COLUMNS = (
    "user_id,common_parcels,rare_parcels,epic_parcels,legendary_parcels,"
    "badge_count,normal_boost_hours_per_day,srb_boost_hours_per_month"
)
GAME_SESSION_COLUMNS = (
    "id,game,event_date,victories,final_position,played_minutes,created_at"
)


class SupabaseError(RuntimeError):
    """A user-safe error returned by Supabase or the network layer."""


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    publishable_key: str
    email_redirect_url: str | None = None

    @classmethod
    def from_environment(cls) -> SupabaseConfig | None:
        load_dotenv(Path(__file__).resolve().with_name(".env"))
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        redirect_url = os.getenv("SUPABASE_EMAIL_REDIRECT_URL", "").strip()
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
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if prefer:
            headers["Prefer"] = prefer
        request = Request(f"{self.url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = {}
            message = detail.get("msg") or detail.get("message") or detail.get("error_description")
            if exc.code == 401:
                raise SupabaseError("La sesión ha caducado. Inicia sesión de nuevo.") from exc
            if exc.code == 429:
                raise SupabaseError("Demasiados intentos. Espera un momento y vuelve a probar.") from exc
            raise SupabaseError(str(message or "Supabase rechazó la operación.")) from exc
        except (TimeoutError, URLError) as exc:
            raise SupabaseError("No se pudo conectar con Supabase. Comprueba tu conexión.") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SupabaseError("Supabase devolvió una respuesta inesperada.") from exc

    def sign_up(self, email: str, password: str) -> dict:
        path = "/auth/v1/signup"
        if self.email_redirect_url:
            path += "?" + urlencode({"redirect_to": self.email_redirect_url})
        return self._request(
            path,
            method="POST",
            payload={"email": email, "password": password},
        )

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "/auth/v1/token?grant_type=password",
            method="POST",
            payload={"email": email, "password": password},
        )

    def get_profile(self, user_id: str, access_token: str) -> dict | None:
        query = quote(user_id, safe="")
        result = self._request(
            f"/rest/v1/profiles?select={PROFILE_COLUMNS}&user_id=eq.{query}",
            access_token=access_token,
        )
        return result[0] if result else None

    def save_profile(self, profile: dict, access_token: str) -> None:
        self._request(
            "/rest/v1/profiles",
            method="POST",
            payload=profile,
            access_token=access_token,
            prefer="resolution=merge-duplicates,return=minimal",
        )

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
