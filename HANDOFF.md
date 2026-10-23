# Atlas Planner - Project Handoff

Last reviewed: 2026-10-23
Workspace: `C:\Users\marti\Documents\GitHub\App_Atlas`
Language of the user: Spanish. Continue in Spanish unless asked otherwise.

## Project Summary

Atlas Planner is a Python/Flet, mobile-first web app/PWA for Atlas Earth players. It estimates gross rent from parcel counts, parcel rarity, badges, ordinary boost and SRB hours; compares parcel purchases and recommends a parcel-count target; and forecasts minigame coin pools, victory thresholds for ranks 25/50/100/500/1500, and personal play time when the account has enough recorded sessions.

There is no official game API available to this project. Supabase is used for accounts, private profiles and game sessions, moderated/anonymized shared training observations, and admin tools. The intended initial audience is small, Spain-only, but the data model is intended to support more users.

## Status Legend

- **DONE (code exists):** implemented in the checked-out code; does not imply live production validation.
- **IN PROGRESS:** uncommitted changes or incomplete manual verification.
- **PENDING:** not implemented or not verified.
- **PROBLEM:** known user-facing failure.
- **UNKNOWN:** cannot be established from the repository or conversation.

## Current Status

### DONE (code exists)

- Gross USD parcel-rent calculator, Spanish parcel-count boost tiers, badge bonus tiers, user-specified ordinary/SRB hours, and unboosted hours at x1.
- Monthly-average SRB model: the user specifies active SRB hours from 0 to 64 per average month. Remaining SRB window is x1; ordinary boost is allocated outside SRB windows. Exact event dates are deliberately not fabricated.
- Purchase simulation and income-based parcel-count recommendations. New parcel rarity uses the weighted average rarity probabilities.
- Historical minigame estimates grouped by event, weighted medians/ranges, two-hour-to-three-hour duration normalization, and chronological backtesting helpers.
- A user can save one session per account/game/date (upsert); profile and result access are intended to be user-scoped by RLS.
- Separate welcome, sign-in and registration views; the page starts at authentication and sign-out returns there (in the current uncommitted `app.py`).
- Calendar pickers for predictions and result registration, requested in Spanish locale. Date/game schedule checks are applied in app/model/SQL code.
- Admin-only navigation, review queue, approval/rejection, pasted-row import and local-registry import with player labels omitted before upload (current uncommitted changes).
- Approved user observations and admin/archive observations can be combined with local records for prediction (current uncommitted changes).
- Supabase schema/policies and three SQL migration files are present.

### IN PROGRESS / NOT LIVE-VERIFIED

- Most recent edits are uncommitted. Admin interface, calendar, shared-training merge, Spanish model messages/confidence labels and import return count need end-to-end browser/Supabase verification.
- User previously ran the SQL migrations and reported that account registration/profile persistence worked in an earlier UI version. Current database migration state and current RPC/policy behavior were not inspected directly.
- User said their account/email is the admin account. The current code uses `is_app_admin()` to reveal the Admin tab, but the newly changed UI and admin RPCs have not been confirmed end-to-end in this review.
- Historical conversation said SQL counts for `minigame_observations` and `minigame_events` were zero at that time. Current counts are **UNKNOWN**; no post-import live DB verification was performed.

### PROBLEM: Sign-in shows a connection error

- Current user-visible report: clicking **Iniciar sesion** shows exactly "No se pudo conectar con Supabase".
- `SupabaseConfig._request()` in `src/atlas_planner/supabase_api.py` displays that text for `TimeoutError`/`URLError`; HTTP errors are handled separately and normally show the Supabase response message. Thus the displayed text points to a transport error in the app process, but its root cause is **UNKNOWN**.
- `.env` exists and was loaded by a local diagnostic; the Supabase host was the configured project, and the key had the `sb_publishable_` prefix. No secret value is recorded here.
- With an elevated diagnostic process, `/auth/v1/settings` returned HTTP 200, a read-only REST request returned HTTP 200, and a password-token request with deliberately fake credentials returned HTTP 400 (expected rejection, and evidence the auth endpoint answered). These checks did not authenticate the user's account.
- A non-elevated tool request was blocked by the agent sandbox with Windows socket error 10013. That is an agent-permission limitation, not proof of failure in the user's normal PowerShell session.
- User attempted to start a second Flet instance on port 8551 and got WinError 10048 because the port was already listening. The user later reported that only one listener remained, but the sign-in connection message persisted.
- No account password was requested or tested. Do not ask the user to disclose it.
- **Next diagnostic:** in the user's normal PowerShell, from this exact workspace, verify a read-only request using the project's `.venv` Python and `SupabaseConfig` (do not print the key). Compare its outcome with the app. If it succeeds, instrument/log the underlying exception type/reason safely or inspect whether the running server is this checkout/configuration. If it fails, investigate local proxy/firewall/DNS/TLS. Do not assume a wrong password explains the exact connection message.

### PENDING / UNKNOWN

- No production host, deployment workflow, HTTPS domain, monitoring, or store package is configured in the repository. A locally running Flet web app is not production deployment.
- The app's live mobile/PWA behavior and Spanish calendar appearance have not been screenshot/manual-tested in this handoff task.
- The data model intentionally excludes positions 1-3 from the forecasting targets; accuracy, especially for sparse 3-hour history, remains limited and should be measured rather than advertised as certainty.

## Exact Point Where Work Stopped

The immediate conversation was troubleshooting Supabase sign-in. The user clarified that the error appears specifically on clicking the sign-in button, then confirmed the exact text: "no se pudo conectar con supabase". The port conflict was a separate issue: a second server could not bind to 8551; the user said they resolved it to one listener, but sign-in still failed.

The active code context also contains recent edits to:

- `src/atlas_planner/app.py`: Spanish calendar widgets, user authentication split, private user result flow, admin review/import UI, shared approved training observations.
- `src/atlas_planner/minigame_model.py`: Spanish schedule errors and confidence values (`baja`, `media`, `alta`).
- `src/atlas_planner/supabase_api.py`: admin importer now returns inserted-row count.
- `tests/test_calculators.py`: confidence assertion changed from `low` to `baja`.
- `tests/test_admin_features.py`: new tests for import parser/privacy and API return count.

Earlier next action suggested to user was to stop duplicate Flet instances and start one on 8551. That resolved the bind conflict according to the user, not the Supabase sign-in error. Do not repeat port killing/restarting as the only diagnosis. First determine whether the exact same `.venv` Python process can reach Auth from the user's normal PowerShell; then diagnose the transport exception in the server process. The elevated probes above are insufficient to declare sign-in fixed.

## Conversation Decisions and Requirements

### Required

- Build a mobile-first Atlas Earth planning app in Python. User has explicitly requested that an assistant not create, modify, or delete files unless the user authorizes it. This handoff file is expressly authorized; subsequent code edits need user approval.
- Initially focus on Spain and the current small user base. Gross USD estimates; currency conversion/PayPal fees are out of scope for now.
- Income inputs: counts of common/rare/epic/legendary parcels, badge count, ordinary boost hours/day, and active SRB hours/month. Apply boost and badge tiers automatically.
- Use average monthly SRB allowance (up to 64 hours), not guessed exact dates. User clarified hours not activated earn x1, as do other hours without boosts.
- Parcel purchase estimate should compare an additional parcel count and recommend either buying to the current boost-tier upper bound or waiting until a more favorable total. Do not estimate time to accumulate parcels yet.
- Minigame observations are historical player samples at rank thresholds 25, 50, 100, 500 and 1500. Ignore top 1-3 targets. Several anonymous rows from one event represent different players; the same player may appear in different events/ranks. Observations are grouped and evaluated as event data, not independent event dates.
- Regular events are Monday, Tuesday, Thursday and Sunday. Historically events lasted 2h; current regular duration is 3h. Last Saturday of each month has 1h events and includes Fishing Vintage. Exact pool/turnout patterns can depend on day-of-month.
- `Racer(V)` is archived, not an active prediction category, but remains representable for historical data in case it returns. `Fishing(V)` is a separate active last-Saturday event type.
- Users submit their own game results to improve the shared model, but identities and personal sessions remain private. New user results must be manually reviewable by the admin before becoming anonymous training observations. Admin can import historical rank-threshold rows without player labels. Avoid direct collection of user account passwords or game credentials.
- Only one result per user/game/date; resubmission overwrites/upserts. Anonymous/admin samples must not duplicate a rank within the same event. DB schedule constraints should reject invalid days/durations.
- Registration fields are chosen username, email and password; email confirmation precedes login. Username is a public handle for predictions, not a game login. Sign-in and registration views are separate. Profile save is in the parcel/profile area. Logout must return to auth screen.
- Date picking should be visual, Spanish locale, Monday-first if supported, and avoid invalid game dates.

### Preferences / decisions

- Supabase chosen because the user knows it from another project. Never put the `service_role` key in the client or `.env` used by the app; use publishable key and authenticated-user JWT/RLS/admin RPC.
- Model goal is useful, honest estimates rather than a high-capacity model unsupported by sparse data. User is open to later comparing curves/ML and leveraging 2h events, but wants validation and more observations for 3h where feasible.
- UI should be clearly separated, centered/responsive, primarily for mobile. Keep the primary workflow gated behind auth.
- Existing R scripts are legacy reference only; app implementation is Python.

### Open questions

- What exactly makes `urlopen` raise `URLError` inside the user's live sign-in action while elevated endpoint diagnostics succeed? Need normal-PowerShell and same-server evidence.
- Are all three migrations applied to the currently configured Supabase project, and do the admin RPCs/policies match this checkout?
- Are model event/observation rows currently loaded into Supabase? Last historical SQL count was zero; current state unknown.
- Which production host/domain and deployment model will be used? Not decided.
- How should 3h prediction confidence/calibration be presented as the new 3h dataset grows? Current model marks duration mismatch/low sample confidence, but forecast accuracy needs broader backtesting.

## Architecture and Data Flow

- Python 3.12, Flet 1.x web app, `python-dotenv`; no separate JS frontend or application server code beyond Flet's embedded web server.
- `src/main.py` calls `ft.run(main)`; `src/atlas_planner/app.py` builds views and invokes domain modules and `SupabaseConfig`.
- Income flow: UI inputs -> `atlas_income.calculate_income` / `simulate_purchase` / `recommend_purchase_strategy` -> USD estimates and breakdown.
- Forecast flow: local `registro_juegos.txt` plus authenticated reads of anonymous shared Supabase observations -> `GameRecord` history -> `predict_event` -> pool, victory thresholds, optional personal time from saved sessions.
- Persistence flow: Flet -> `SupabaseConfig` REST/Auth HTTP calls -> Supabase Auth/PostgREST/RPC -> RLS and SQL triggers/functions. Account and player result rows are user scoped. Admin functions check `app_admins`; reviewed player observations are copied into anonymous global model tables.
- Client authentication uses `.env` `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, optional `SUPABASE_EMAIL_REDIRECT_URL`. Never record values in docs/issues.
- Current local run command: `\.venv\Scripts\flet.exe run --web --port 8551 --assets assets src\main.py`; open `http://127.0.0.1:8551/`. Only one process may bind that port. Flet hot reload can be enabled with `--directory --recursive` while diagnosing/reviewing live edits.

## Relevant Structure

```text
assets/                         PWA manifest, favicon and install icons
data/private/                   Local excluded-player data; private, not app runtime input
research/legacy_r/              Original R references
src/main.py                     Flet entrypoint
src/atlas_planner/app.py        UI, auth, calendars, results, admin and view handlers
src/atlas_planner/atlas_income.py Income rules and parcel-purchase recommendations
src/atlas_planner/minigame_model.py Event parsing, analog forecasts, pace and backtest
src/atlas_planner/supabase_api.py Auth, profile/session REST and admin RPC client
src/atlas_planner/data/registro_juegos.txt Local historical dataset (ignored by Git)
supabase/migrations/             SQL schema, RLS, schedule guards, review/admin RPCs
supabase/SETUP.md                Migration/admin setup instructions
tests/                           unittest coverage
README.md                        Local setup/run/test commands
.env.example                     Environment variable names/placeholders only
.gitignore                       Excludes secrets, local datasets, virtualenv and build output
HANDOFF.md                       This operational handoff
```

The local registry currently parses to **356 rows, 94 distinct events**: Bowling 64 rows, Warship 73, Racer(V) 4, Golf 59, Fishing(V) 25, Racer 72, Fishing 59. Duration counts: 249 rows of 120m, 77 of 60m, 30 of 180m. These are local-file counts, not live DB counts; no individual player names are reproduced here. `data/private/jugadores_excluidos.txt` exists and is ignored by Git.

Important privacy/deployment caveat: `.gitignore` excludes `src/atlas_planner/data/registro_juegos.txt`, and `git ls-files` confirms it is not tracked. A fresh clone/deploy will therefore not contain this local training archive. Either keep it private and import approved anonymous rows into Supabase with the admin interface, or explicitly decide on an appropriately private/secure distribution mechanism. Do not remove ignore rules or publish raw player data without explicit authorization/consent.

## Important Code and Business Logic

- `atlas_income.py`
  - Rent/second: common 1.1e-9 USD, rare 1.6e-9, epic 2.2e-9, legendary 4.4e-9.
  - Average rarity weights: 50%/30%/15%/5% (used for new parcel assumptions).
  - Badge bonus tiers: 0 => 0%; 1-10 => 5%; 11-30 => 10%; 31-60 => 15%; 61-100 => 20%; 101+ => 25%.
  - Spain normal boost tiers: 1-70 x20; 71-100 x15; 101-135 x10; 136-170 x8; 171-200 x7; 201-250 x6; 251-300 x5; 301-350 x4; 351-400 x3; 401+ x2.
  - `_period_hours()` prorates the average 64h SRB window and active hours; unused boost time contributes at x1. Avoid changing this to 24h normal-boost treatment during SRB; user specifically corrected that assumption.
  - `recommend_purchase_strategy()` is income-only and does not infer the user's future savings/time.
- `minigame_model.py`
  - `GameRecord` contains game/date/rank/victories/event pool/coins earned/played time/duration/player label.
  - `infer_event_duration_minutes()` enforces active event schedule; normal events infer 180m, last-Saturday events 60m; legacy rows may explicitly be 120m.
  - `_events()` groups all observations by `(event_date, game)` and rejects inconsistent pool/duration metadata.
  - `estimate_event_pool()` and `estimate_victories_for_position()` use weighted medians/quantiles, day-of-month and pool similarity, duration scaling, and event-level samples. Exact 1500-rank observations get a special median treatment.
  - `_confidence()` currently returns Spanish `baja`/`media`/`alta`; callers and tests must use the same enum/text. Do not restore English strings without changing UI/tests.
  - `estimate_player_pace()` uses named local archive records; `estimate_saved_session_pace()` uses only the selected user's saved sessions. `predict_event()` returns no personal time estimate if no pace history exists.
  - `backtest_by_event()` evaluates chronologically and reports MAE for forecasts and simple baselines.
- `app.py`
  - `_admin_import_rows()` accepts ISO, DD-MM-YYYY and DD/MM/YYYY, ignores player labels, validates ranks/schedule/duration and rejects duplicates in the pasted batch.
  - `_training_records()` maps anonymous Supabase rows to `GameRecord(player=None)`.
  - `authenticate()`, `sign_out()`, calendar callbacks, `save_game_result()`, `game_result()`, `income_result()`, and admin helpers build the feature flows. Most of these changes are uncommitted.
  - The auth UI currently converts `SupabaseError` to `auth_feedback`. It starts at `auth_root`, then swaps to `app_root` on success; logout clears session and returns to auth.
- `supabase_api.py`
  - `SupabaseConfig.from_environment()` loads `.env` rooted from package path.
  - `_request()` maps network timeouts/`URLError` to the exact generic connection message. HTTP errors use server-provided message where possible; 401/429 have special messages.
  - Auth methods: `/auth/v1/signup`, `/auth/v1/token?grant_type=password`; account signup includes configured redirect URL.
  - Admin methods call `is_app_admin`, `admin_pending_game_results`, `admin_review_game_result`, `admin_import_model_rows` RPCs.
- SQL migrations:
  - `0001`: profiles, event and anonymous observation tables, private sessions, RLS.
  - `0002`: game/date/final position on sessions and unique per-user/game/date index; allows results without an existing global event row.
  - `0003`: username/profile trigger, admin table/role RPC, result moderation, schedule validation, anonymous source keys and admin import/review RPCs.

## Dependencies and Execution

- Current local environment: Python 3.12.10; Flet/Flet CLI/Flet web 1.0.1; python-dotenv 1.2.3. Declared in `pyproject.toml` as `flet>=1.0,<2`, `python-dotenv>=1,<2`; dev extras include `flet-cli` and `flet-web`.
- Setup from README: `python -m venv .venv`, `\.venv\Scripts\python.exe -m pip install -e ".[dev]"`, copy `.env.example` to `.env` if needed, then configure variable names above.
- Run: `\.venv\Scripts\flet.exe run --web --port 8551 --assets assets src\main.py`.
- Tests: `\.venv\Scripts\python.exe -m unittest discover -s tests -v`.
- Supabase: execute migrations in numeric order, then grant the admin account via SQL as described in `supabase/SETUP.md`. That doc instructs using the exact account email in SQL; do not put service-role key into app config.
- No lockfile, deployment workflow, CI config, Dockerfile or host config was found in tracked files.

## Tests and Verification

- **PASSED:** `python -m unittest discover -s tests -v` using project `.venv`: 19 tests, all passing on 2026-09-27.
- Tests cover income tier boundaries, unboosted x1/SRB behavior, event schedule/legacy duration normalization, rank predictions/backtest, session pace, Supabase request construction/RLS filters, import parsing/privacy and admin import count parsing.
- **PASSED (limited network diagnostics):** Auth settings GET 200; profile REST GET 200; fake login POST 400. No real credentials used. Does not prove actual account login or the Flet server's networking environment.
- **FAILED for a second server only:** trying to bind port 8551 while already occupied gave WinError 10048. This was not a Supabase service error. User reports they reduced it to one listener.
- **NOT TESTED:** successful login on current UI, email-confirmed status, live profile read/save in current version, admin role RPC/panel/import/review on the configured DB, actual calendar rendering, production/mobile HTTPS install, full deployment, fresh-clone behavior without private local dataset.

## Git State (before adding this document)

- Branch: `main`, tracking `origin/main`; both were at `95019dc` (`Actualizacion beta`) at inspection time.
- Recent commits: `95019dc Actualizacion beta`, `9664f83 Segunda version actualizada`, `1357a19 Primera version funcional`, `b2149fa Mas anadidos`, `e637fb3 Primera subida`.
- Modified tracked files: `src/atlas_planner/app.py`, `src/atlas_planner/minigame_model.py`, `src/atlas_planner/supabase_api.py`, `tests/test_calculators.py`.
- Untracked before this task: `tests/test_admin_features.py`.
- No tracked deletions were present. Local `.env`, historical registry and excluded-player data are ignored, not tracked. `.flet` build/temp files are ignored; a recursive listing hit access denied in some generated temp folders. No attempt was made to inspect or change those generated files.
- The app diff is substantial (~646 lines by `git diff --stat`) and represents intended feature work in this conversation; preserve it. `minigame_model.py`/test confidence changes keep Spanish output consistent. `supabase_api.py` now returns imported count. Do not reset, checkout, clean, stage, commit or push these changes.
- `HANDOFF.md` is newly created by this task and will itself appear untracked. No commit or push was made.

## Known Risks / Do Not Repeat

- Do not kill a process based only on a port PID until its identity is confirmed. WinError 10048 means the port is occupied; it does not mean Supabase is down. Stop an app through its own terminal with Ctrl+C when possible, then run a single instance.
- Do not treat elevated agent HTTP tests as proof of the user's in-app login; the actual account credentials were not used.
- Do not publish `.env`, raw registry, excluded-player file, or player names. `.env.example` placeholders are safe to track; actual `.env` must remain ignored.
- Do not add a service-role credential to Python client code. Admin authorization belongs in secured SQL/RPC and authenticated user JWT checks.
- Do not treat multiple rows from one event as independent events when training/backtesting. Pool/duration metadata must match for all rows sharing date+game.
- Do not predict top-three rewards; do not claim 3h estimates are strongly calibrated on the current sparse archive.
- Do not invent exact SRB dates; calculate using user-provided active hours against the approximate 64h/month window.
- Do not remove the `Racer(V)` archival records merely because the event is inactive.
- Do not make code edits without explicit user authorization, except that this handoff file was explicitly requested.

## NEXT STEPS

1. Continue in Spanish and preserve all existing worktree changes.
2. Diagnose sign-in from the user's normal PowerShell, using the exact project `.venv` and current `.env`, without revealing credentials. Test a read-only Auth settings request and report only success/status or exception type/reason. This will tell whether network restrictions apply outside the elevated agent diagnostic.
3. If that request succeeds, determine whether the running Flet server is this workspace/process and capture the underlying exception from the sign-in call safely. The generic UI message is generated for `URLError`; do not conflate it with bad credentials (HTTP error path).
4. After connection diagnosis, ask user before any code change. If approved, make the smallest diagnostic/error-handling change, test it, then remove or reduce diagnostics before production.
5. Once login succeeds, verify profile/session calls and admin RPCs against the configured Supabase project. Confirm migrations 0001-0003, role membership and RLS; never request the user's password.
6. Test calendar and admin UI in a browser. Validate invalid dates, Saturday/Fishing(V), result upsert, duplicate import protection, review approval/rejection, and no identity in training rows.
7. Import historical rows through the admin path if the user authorizes it; verify resulting event/observation counts in Supabase. Because the raw archive is Git-ignored, plan how fresh deploys receive model data without publishing player-identifying data.
8. Re-run the full unit suite and backtest by duration/event; report the sample count and MAE before representing confidence as calibrated.
9. Only then plan HTTPS hosting/PWA install/production hardening. No production host has been chosen.

## FIRST ACTION

Ask the user to run a read-only Auth connectivity check from the same normal PowerShell session and project folder used to start Flet (using `.venv` Python and `SupabaseConfig`, printing no key), then compare that result with the app's sign-in `URLError`. The root cause is still unknown; do not edit code or kill processes without approval.

## Audit update (2026-10-23)

The user authorized a full repository review and fixes based on this handoff. Preserve pre-existing worktree changes; no commit or push has been made.

- Added refresh-token persistence with Flet `SharedPreferences`, refresh-grant restore on startup, token rotation, logout clearing, and disabled auth controls while restore is pending. The refresh token is stored in browser local storage and is therefore readable by same-origin JavaScript; never log it. This is a known client-side limitation, not equivalent to an HttpOnly server session.
- Profile payload now includes required username and validates it; profile upsert requests the representation and fills the form from the persisted response.
- Raw local training registry was moved, without changing contents, from the Python package to `data/private/registro_juegos.txt`, which is Git-ignored. Desktop development loads it; Pyodide/browser explicitly never loads it. The static PWA should train from approved anonymous Supabase rows only.
- Added GitHub Pages workflow and deployment checklist in README. It builds with the repository subpath as `--base-url` and deploys on pushes to `main` or manual workflow dispatch.
- Unit suite passes (28 tests at this audit); `compileall` passes. Static `flet publish` completed using Python 3.12/Pyodide 0.27.8 and its clean package contains neither the raw registry nor nested prior builds.
- NOT verified against live Supabase credentials/database, public GitHub Pages settings, email redirects or a real mobile browser. User must push through VS, choose GitHub Actions under repository Pages settings, configure Supabase Site URL/Redirect URLs for the exact Pages URL, and manually test flows after the first deployment.
- No GitHub workflow was triggered by this audit; no commit/push was made. PWA install remains the final manual check as requested.

The older sign-in diagnostic and “FIRST ACTION” sections above document the historical state from 2026-09-27 and are superseded by this update for current code. Live authentication still needs confirmation after publication.

## Confidence and Contradictions

- **CONFIRMED:** source code, current tests, Git state and local dataset aggregate counts as described above.
- **CONFIRMED, limited:** network probes from an elevated agent process received valid HTTP responses from the configured Supabase project.
- **CONFIRMED from user report:** after the duplicate-listener episode, the user said one listener was active but login still displayed a Supabase connection error.
- **UNKNOWN:** cause of the user's sign-in transport error, current live DB row counts/schema, whether migrations are present on the exact live project, successful current admin workflow and production readiness.
- Possible distinction, not a contradiction: historical user reports said earlier registration/profile persistence had worked; current edited auth view now reports a connection error on sign-in. This may reflect changed runtime/config/network or another transient condition. No evidence establishes which.
- Code includes current admin/calendar functionality, but its presence and unit tests do not prove it has been applied/verified against the live Supabase project.
