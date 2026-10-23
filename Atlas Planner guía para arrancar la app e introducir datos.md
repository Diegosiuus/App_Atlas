# Atlas Planner: guía para arrancar la app e introducir datos

Guía basada en el README, `supabase/SETUP.md` y los dos handoffs (27 y 29 de septiembre de 2026). Todos los comandos se ejecutan en PowerShell desde la carpeta del proyecto.

## 0. Antes de nada

```powershell
cd "C:\Users\marti\Documents\GitHub\App_Atlas"
```

No hace falta activar el entorno virtual: se usa siempre la ruta `.\.venv\Scripts\...`. Solo debe haber **una** instancia de la app en marcha a la vez.

## 1. Modo desarrollo (el más rápido para probar cambios)

```powershell
.\.venv\Scripts\flet.exe run --web --port 8551 --assets assets src\main.py
```

Abre `http://127.0.0.1:8551`. Lee las claves del archivo `.env`. Para parar la app, pulsa **Ctrl+C** en esa misma terminal.

Si aparece **WinError 10048**, el puerto 8551 ya está ocupado por otra instancia. Cierra la terminal anterior con Ctrl+C antes de lanzar otra; no mates procesos por su PID sin comprobar qué son.

## 2. Modo PWA (el que se parece al producto final)

```powershell
.\.venv\Scripts\flet.exe publish -v
.\.venv\Scripts\flet.exe serve dist
```

Abre `http://localhost:8000`. Este modo corre en el navegador (Pyodide), usa `config_public.py` en lugar de `.env` y hace las peticiones a Supabase con `fetch()`.

Cada vez que cambies código, repite `publish` y reinicia `serve`. En el navegador recarga sin caché con **Ctrl+F5** para no ver una versión antigua del service worker.

## 3. Pruebas automáticas

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 4. Introducir datos

### 4.1 Tus propios resultados (cualquier usuario)

1. Inicia sesión.
2. Ve a la pestaña **Resultados**.
3. Indica minijuego, fecha del evento, victorias, puesto final (opcional) y minutos jugados.
4. Pulsa **Guardar resultado**. Solo se permite un resultado por minijuego y fecha; si lo repites, se actualiza.

El resultado queda **privado y pendiente** hasta que el administrador lo apruebe.

### 4.2 Aprobar resultados (solo administrador)

1. Entra con la cuenta administradora. Debe estar dada de alta en `app_admins` (ver 4.4).
2. En la pestaña de administración, abre la cola de resultados pendientes.
3. Para aprobar, indica la bolsa total de monedas y la duración del evento. Solo se entrenan puestos entre 4 y 1500.

### 4.3 Importar histórico (solo administrador)

Filas separadas por espacios, con este orden de columnas (el mismo que `registro_juegos.txt`):

`juego fecha puesto victorias monedas_totales_evento monedas_ganadas duracion_min [jugador]`

El nombre de jugador se ignora y nunca se sube. Las filas repetidas se descartan solas. También existe la opción de importar el registro local sin nombres.

### 4.4 Dar permiso de administrador (una sola vez)

En el SQL Editor de Supabase, con el correo exacto de tu cuenta:

```sql
insert into public.app_admins (user_id)
select id from auth.users where lower(email) = lower('TU_CORREO')
on conflict (user_id) do nothing;
```

Debe insertar exactamente una fila. Las migraciones `0001`, `0002` y `0003` deben estar ejecutadas, en ese orden.

## 5. Reglas que no se deben romper

- Nunca pongas la clave `service_role` en el cliente, en `.env` ni en `config_public.py`.
- No subas a GitHub `.env`, `registro_juegos.txt` ni `jugadores_excluidos.txt`.
- No compartas contraseñas ni tokens al pedir ayuda.

## 6. Si algo falla

- **Se ve una versión antigua:** vuelve a ejecutar `publish -v` y recarga con Ctrl+F5.
- **Error de conexión con Supabase:** comprueba si ejecutas el modo desarrollo o el modo PWA; cada uno usa un cliente HTTP distinto.
- **No aparece la pestaña de administración:** la cuenta no está en `app_admins` o falta una migración.
