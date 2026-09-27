# Atlas Planner

Python mobile-first PWA prototype. Income profiles and personal game results synchronize with Supabase Auth and private user tables. Public prediction observations still come from the local `registro_juegos.txt`.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "flet>=1,<2" "flet-cli>=1,<2" "flet-web>=1,<2" "python-dotenv>=1,<2"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Add your Supabase publishable key and allowed email redirect URL to .env
.\.venv\Scripts\flet.exe run --web --port 8551 app.py
```

Open `http://127.0.0.1:8550` in a browser. For phone installation, the deployed site must use HTTPS; local HTTP is for development only.

Create an account or sign in from **Mi cuenta**. If email confirmation is enabled in Supabase, confirm the address before signing in. The current session is held in memory and is cleared when the app session ends. Profile rows are protected by the table's row-level security policies; never put a `service_role` key in this app.

Run `supabase/migrations/0002_player_game_results.sql` in the Supabase SQL Editor before using **Resultados**. Each account can save one result per minigame and event date; saving that event again updates it. Personal results are private and are used only to estimate that user's pace.
