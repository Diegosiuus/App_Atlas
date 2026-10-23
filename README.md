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

## Publicación en GitHub Pages

El workflow `.github/workflows/deploy-pages.yml` compila la PWA y la publica en cada `push` a `main` (o manualmente desde **Actions**). En GitHub, abre **Settings > Pages** y selecciona **GitHub Actions** como fuente. La URL esperada sigue el formato `https://<usuario>.github.io/App_Atlas/`; usa el enlace exacto que muestre Pages.

Antes de permitir registros en producción:

1. En Supabase, configura **Authentication > URL Configuration > Site URL** con la URL de Pages y añade esa misma dirección a **Redirect URLs**.
2. Comprueba que las migraciones `0001`-`0003` están aplicadas y que las políticas RLS/RPC de administrador son las esperadas.
3. Ejecuta el workflow y verifica registro, confirmación por correo, inicio/cierre de sesión tras recargar, perfil, resultados y acceso administrativo desde HTTPS.
4. Importa desde la cuenta administradora las observaciones anónimas aprobadas. El archivo local `data/private/registro_juegos.txt` no se publica ni forma parte del entrenamiento del navegador.

GitHub Pages es público y sirve archivos estáticos: el código Python/JavaScript puede inspeccionarse y la publishable key del cliente no es un secreto. La privacidad y los permisos deben depender de Supabase RLS/RPC. Nunca añadas una `service_role` key al cliente.

## Estructura

```text
assets/                     Iconos y recursos web
data/private/               Datos auxiliares privados, fuera del paquete de la app
research/legacy_r/          Scripts originales de análisis en R
src/atlas_planner/          Código de la aplicación y modelo
src/main.py                 Entrada de Flet
supabase/migrations/        Esquema y políticas versionadas de Supabase
tests/                      Pruebas automatizadas
```

`data/private/registro_juegos.txt` contiene el histórico privado usado en desarrollo. Nunca se empaqueta en la PWA; en el navegador el modelo se alimenta de observaciones anónimas aprobadas en Supabase. `data/private/jugadores_excluidos.txt` tampoco se carga en la app. No publiques datos personales ni secretos en el repositorio.
