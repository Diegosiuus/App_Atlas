import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas_planner.auth_session import (
    REFRESH_TOKEN_KEY,
    clear_refresh_token,
    load_refresh_token,
    store_refresh_token,
)


class MemoryPreferences:
    def __init__(self):
        self.values = {}

    async def set(self, key, value):
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def remove(self, key):
        self.values.pop(key, None)
        return True


class AuthSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_token_round_trip_is_namespaced(self):
        preferences = MemoryPreferences()

        await store_refresh_token(preferences, "rotating-token")

        self.assertEqual(await load_refresh_token(preferences), "rotating-token")
        self.assertEqual(set(preferences.values), {REFRESH_TOKEN_KEY})

    async def test_clear_removes_only_atlas_session(self):
        preferences = MemoryPreferences()
        preferences.values["other-app-setting"] = "keep"
        await store_refresh_token(preferences, "rotating-token")

        await clear_refresh_token(preferences)

        self.assertIsNone(await load_refresh_token(preferences))
        self.assertEqual(preferences.values, {"other-app-setting": "keep"})

    async def test_empty_refresh_token_is_rejected(self):
        with self.assertRaises(ValueError):
            await store_refresh_token(MemoryPreferences(), "")


if __name__ == "__main__":
    unittest.main()
