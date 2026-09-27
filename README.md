# Atlas Planner

Aplicación web instalable, pensada primero para móvil, para estimar ingresos de Atlas Earth y analizar resultados de minijuegos. La autenticación y los perfiles usan Supabase; los registros aprobados y anónimos alimentan las predicciones compartidas.

## Requisitos

- Python 3.12
- Proyecto Supabase con las migraciones de `supabase/migrations` aplicadas
- Clave publishable de Supabase en `.env` (nunca uses una clave `service_role` en el cliente)

## Desarrollo local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Completa SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY y SUPABASE_EMAIL_REDIRECT_URL en .env
.\.venv\Scripts\flet.exe run --web --port 8551 --assets assets src\main.py
```

Abre `http://127.0.0.1:8551`. Para instalar la PWA en el móvil, el sitio publicado debe servirse mediante HTTPS; la dirección local es solo para desarrollo.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Estructura

```text
assets/                     Iconos y recursos web
data/private/               Datos auxiliares privados, fuera del paquete de la app
research/legacy_r/          Scripts originales de análisis en R
src/atlas_planner/          Código de la aplicación y datos de entrenamiento locales
src/main.py                 Entrada de Flet
supabase/migrations/        Esquema y políticas versionadas de Supabase
tests/                      Pruebas automatizadas
```

`src/atlas_planner/data/registro_juegos.txt` contiene las observaciones usadas localmente por el modelo. `data/private/jugadores_excluidos.txt` se conserva como dato de investigación local y no se carga en la app. No publiques datos personales ni secretos en el repositorio.
