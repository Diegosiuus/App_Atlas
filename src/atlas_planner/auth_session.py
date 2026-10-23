"""Small helpers for keeping a Supabase refresh token across PWA reloads.

Flet's browser preferences use local storage; the refresh token is therefore
available to same-origin JavaScript and must never be logged or exposed.
"""

from __future__ import annotations


REFRESH_TOKEN_KEY = "com.atlasplanner.auth.refresh_token.v1"


async def store_refresh_token(preferences, refresh_token: str) -> None:
    if not refresh_token:
        raise ValueError("Supabase did not return a refresh token.")
    if not await preferences.set(REFRESH_TOKEN_KEY, refresh_token):
        raise RuntimeError("The browser could not persist the session token.")


async def load_refresh_token(preferences) -> str | None:
    token = await preferences.get(REFRESH_TOKEN_KEY)
    return token if isinstance(token, str) and token else None


async def clear_refresh_token(preferences) -> None:
    await preferences.remove(REFRESH_TOKEN_KEY)
