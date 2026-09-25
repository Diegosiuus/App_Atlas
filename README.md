# Atlas Planner

Python mobile-first PWA prototype. The current minigame calculator reads the local `registro_juegos.txt`; Supabase tables are prepared separately and are not connected to the interface yet.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "flet>=1,<2" "flet-cli>=1,<2" "flet-web>=1,<2"
.\.venv\Scripts\flet.exe run --web --port 8550 app.py
```

Open `http://127.0.0.1:8550` in a browser. For phone installation, the deployed site must use HTTPS; local HTTP is for development only.
