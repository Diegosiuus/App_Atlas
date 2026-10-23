# HANDOFF DE CONTINUACIÓN --- App Atlas Planner (PWA)

**Fecha de actualización:** 29 de septiembre de 2026\
**Proyecto local:** `C:\Users\marti\Documents\GitHub\App_Atlas`\
**Objetivo inmediato:** corregir la persistencia de sesión y el guardado
del perfil antes de publicar la PWA en un enlace público.

> Este documento complementa el `HANDOFF.md` anterior. Usa el anterior
> para el contexto histórico y general del proyecto; este documento
> recoge el estado técnico más reciente y debe considerarse la
> referencia para los avances descritos aquí. Si hay contradicciones con
> el código actual, inspecciona el repositorio y pregunta antes de
> asumir cambios.

### Nota de auditoría posterior (23 de octubre de 2026)

El usuario autorizó revisar y corregir el repositorio tomando este handoff como referencia. Se implementó persistencia/restauración de sesión con `SharedPreferences`, guardado de perfil con confirmación del registro devuelto por Supabase y aislamiento del registro local de jugadores fuera del paquete de la PWA. El histórico local permanece en `data/private/registro_juegos.txt` y no se empaqueta; el navegador usa datos compartidos aprobados de Supabase. Se añadió el workflow de GitHub Pages en `.github/workflows/deploy-pages.yml` y se documentaron los pasos en `README.md`.

Verificación local: 28 pruebas unitarias, compilación Python y `flet publish` con Python 3.12 correctos; se comprobó que el archivo privado y compilaciones anteriores no aparecen en el paquete limpio. No se ha hecho push ni se ha verificado el sitio público, la configuración del panel de Supabase, ni el comportamiento real del token tras recargar en móvil. El refresh token se guarda en local storage y no ofrece la misma protección que una sesión HttpOnly de servidor.

------------------------------------------------------------------------

## 1. Resumen ejecutivo

La aplicación Atlas Planner está desarrollada en Python con Flet y
utiliza Supabase para autenticación y almacenamiento de datos. Se está
preparando como **PWA**, no como aplicación nativa.

Se consiguió publicar el paquete web mediante `flet publish` y
ejecutarlo localmente mediante `flet serve dist`. Se resolvió un
problema de comunicación con Supabase en el entorno Pyodide: las
peticiones realizadas con `urllib.request` no funcionan para HTTPS en el
navegador y la llamada a `fetch()` estaba enviando mal las opciones de
la petición. Se corrigió la conversión de las opciones Python a un
objeto JavaScript con `pyodide.ffi.to_js`.

### Estado funcional comprobado por el usuario

-   El inicio de sesión funciona.
-   Cerrar sesión y volver a iniciar sesión funciona.
-   Se pueden crear cuentas y confirmar el registro por correo.
-   Una contraseña incorrecta produce un error comprensible; por ahora
    es aceptable que el mensaje incluya un JSON o información técnica.
-   El usuario indica que el resto de funcionalidades probadas funciona,
    con dos excepciones pendientes:
    1.  **Al recargar la página se pierde la sesión.**
    2.  **Los cambios del perfil parecen no guardarse o no persistir
        correctamente.**
-   La instalación como PWA todavía no se ha probado. Se dejará para el
    final.

No se ha verificado aún el comportamiento en el dominio público
definitivo ni la instalación en un dispositivo móvil.

------------------------------------------------------------------------

## 2. Entorno técnico

-   Sistema operativo: Windows.

-   Python del entorno del proyecto: Python 3.12.x.

-   Flet: `1.0.1`.

-   `flet-cli`: `1.0.1`.

-   `flet-web`: `1.0.1`.

-   Flutter instalado por Flet: `3.44.8`.

-   Pyodide usado por `flet publish`: `0.27.8`.

-   Base de datos/autenticación: Supabase.

-   Directorio de trabajo:

    ``` powershell
    cd "C:\Users\marti\Documents\GitHub\App_Atlas"
    ```

Dependencias del `pyproject.toml` conocido:

``` toml
[project]
name = "atlas-planner"
version = "0.1.0"
description = "Mobile-first Atlas Earth planning calculators"
requires-python = ">=3.12,<3.13"
dependencies = ["flet>=1.0,<2", "python-dotenv>=1,<2"]

[project.optional-dependencies]
dev = ["flet-cli>=1.0,<2", "flet-web>=1.0,<2"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
atlas_planner = ["data/*.txt"]

[tool.flet]
org = "com.atlasplanner"

[tool.flet.app]
path = "src"

[tool.flet.web]
route_url_strategy = "hash"
renderer = "canvaskit"
pwa_background_color = "#F2F6F5"
pwa_theme_color = "#087F72"
```

Inspecciona el `pyproject.toml` actual antes de cambiarlo, ya que podría
haber sido modificado desde esta anotación.

------------------------------------------------------------------------

## 3. Configuración de Supabase para el navegador

El proyecto usa un `.env` local con estas variables (los valores reales
no se incluyen aquí):

``` dotenv
SUPABASE_URL=...
SUPABASE_PUBLISHABLE_KEY=...
SUPABASE_EMAIL_REDIRECT_URL=...
```

Se comprobó que la clave tiene formato de **Supabase Publishable Key**
(`sb_pub...`) y que la URL y la clave funcionan desde PowerShell: el
endpoint `/auth/v1/health` respondió HTTP 200.

Como el archivo `.env` no se incluye automáticamente en el paquete
publicado, se creó:

`src\atlas_planner\config_public.py`

con una estructura equivalente a:

``` python
SUPABASE_URL = "..."
SUPABASE_PUBLISHABLE_KEY = "..."
```

El archivo real contiene los valores del proyecto y no debe copiarse a
conversaciones, repositorios públicos ni registros de depuración sin
revisar antes qué contiene. Una URL de Supabase y una publishable key
están destinadas al cliente, pero la seguridad de los datos debe
depender de las políticas RLS y de las comprobaciones de autorización
del servidor. **No incluir nunca una `service_role` key ni una clave
secreta en el cliente.**

En `supabase_api.py`, `SupabaseConfig.from_environment()` intenta cargar
el `.env` para desarrollo local y usa `config_public.py` como
alternativa cuando las variables no están disponibles:

``` python
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
```

Este es el código conocido de una etapa anterior; compara con el fichero
actual antes de reemplazarlo. Observación pendiente:
`SUPABASE_EMAIL_REDIRECT_URL` no está en `config_public.py`; cuando se
conozca el dominio público habrá que comprobar y configurar los enlaces
de confirmación y recuperación de contraseña en Supabase y en la app.

------------------------------------------------------------------------

## 4. Corrección importante de `supabase_api.py` para Pyodide

### Problema inicial

El código de peticiones usaba `urllib.request.urlopen`. En el entorno
Pyodide de la PWA fallaba con:

``` text
URLError: unknown url type: https
```

Por ello, `_request()` detecta el entorno con:

``` python
if sys.platform == "emscripten":
```

y utiliza `fetch()` del navegador. La rama normal de Windows mantiene
`urllib`.

Después, Supabase devolvió errores indicando que no encontraba `apikey`.
Se añadió el parámetro `apikey` a la URL como alternativa a la cabecera:

``` python
web_url = url
separator = "&" if "?" in web_url else "?"
web_url = (
    f"{web_url}{separator}"
    f"apikey={quote(self.publishable_key, safe='')}"
)
```

La URL resultante para el login conserva `grant_type=password` y añade
`apikey`.

### Corrección que permitió que el login funcionara

El diccionario Python de opciones de `fetch()` se pasaba directamente a
JavaScript. Se cambió a `to_js(options)`, importando:

``` python
from pyodide.ffi import to_js
```

La función `browser_request()` que se indicó al usuario fue:

``` python
async def browser_request():
    from pyodide.ffi import to_js

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
```

El código espera el resultado mediante `asyncio.run(browser_request())`
y procesa el estado y el cuerpo HTTP. Esta corrección permitió que el
inicio de sesión funcionara correctamente en la PWA local.

**Importante:** inspecciona la versión actual de `_request()` antes de
modificarla. No vuelvas a sustituirla por una versión anterior ni
elimines la rama de Pyodide que ya funciona. También comprueba si el uso
de `asyncio.run()` presenta problemas en otros flujos; no cambies ese
mecanismo sin reproducir el problema.

### Diagnóstico HTTP

Durante la depuración, se cambió temporalmente el error genérico por uno
que incluye el estado y el cuerpo de Supabase:

``` python
raise SupabaseError(
    f"Supabase respondió HTTP {status}: "
    f"{raw_text or 'Respuesta sin cuerpo'}"
)
```

Se observó un HTTP 405 con cuerpo vacío antes de corregir la conversión
con `to_js()`. Tras aplicar la corrección y republicar, el usuario
confirmó que el inicio de sesión funciona. Conserva mensajes
diagnósticos útiles mientras se depura, pero evita mostrar detalles
técnicos innecesarios al usuario final cuando la app esté lista.

------------------------------------------------------------------------

## 5. Cómo publicar y probar localmente

Desde la raíz del proyecto, con el entorno virtual del proyecto:

### Crear/publicar el paquete web

``` powershell
.\.venv\Scripts\flet.exe publish -v
```

El comando empaqueta la aplicación en `dist` y prepara los recursos que
necesita el navegador/Pyodide. La ejecución anterior completó
correctamente y mostró operaciones como empaquetar la aplicación y
modificar `index.html` y `manifest.json`.

### Servir el paquete generado

``` powershell
.\.venv\Scripts\flet.exe serve dist
```

Después, abrir:

``` text
http://localhost:8000
```

Si se modifica código de la app, volver a ejecutar `flet publish -v` y
reiniciar el servidor si procede. Asegurarse de probar el contenido
recién generado y de recargar el navegador sin usar recursos obsoletos.

No se ha elegido ni configurado todavía un hosting definitivo. El
objetivo final es disponer de un enlace público propio y permitir
instalar la aplicación como PWA.

------------------------------------------------------------------------

## 6. Tareas pendientes prioritarias

### A. Persistencia de sesión tras recargar

**Comportamiento observado:** el usuario inicia sesión correctamente,
pero al recargar la página queda desconectado.

**Hipótesis por comprobar:** el estado de autenticación se mantiene solo
en memoria y se pierde cuando la aplicación Python/Pyodide vuelve a
iniciarse. Todavía no está demostrado que sea esa la causa.

**Investigar:**

1.  Cómo se guarda actualmente el estado de sesión en la app.
2.  Si se conserva `access_token`, `refresh_token` y la información
    necesaria para restaurar al usuario.
3.  Si hay algún almacenamiento de Flet o del navegador ya utilizado en
    el proyecto.
4.  Si la inicialización de la app intenta recuperar la sesión antes de
    mostrar el login.
5.  Cómo renovar el token al caducar y cómo tratar una sesión inválida.

La solución debe usar almacenamiento apropiado del navegador en Pyodide
y manejar los tokens de manera coherente. No basta con guardar un
booleano o el correo. Evitar introducir tokens en logs o mostrarlos en
pantalla. Evaluar cuidadosamente los riesgos de almacenamiento accesible
desde JavaScript; no afirmar que `localStorage` sea seguro frente a XSS.
Si se usa, documentar ese riesgo y no guardar secretos de servidor.

### B. Guardado del perfil

**Comportamiento observado:** el usuario puede registrarse, confirmar la
cuenta y entrar, pero parece que los cambios del perfil no se guardan
correctamente.

**No se ha identificado todavía la causa.** No asumir que es un problema
de Supabase ni modificar las políticas de seguridad sin pruebas.

El siguiente paso acordado con el usuario es pedir e inspeccionar estos
fragmentos del código actual:

1.  Método completo `save_profile()` en
    `src\atlas_planner\supabase_api.py`.
2.  Método completo `get_profile()` en el mismo fichero.
3.  Lugar de la app donde se llama a `save_profile()`, incluida la
    lectura de valores del formulario y la gestión del mensaje de
    éxito/error.

A continuación:

-   comprobar qué datos se envían;
-   comprobar qué usuario/token se utiliza;
-   verificar si la operación espera una respuesta válida y si los
    errores se muestran o se silencian;
-   verificar cómo se recupera el perfil después de guardar;
-   si hace falta, inspeccionar la respuesta HTTP y las políticas RLS de
    la tabla correspondiente en Supabase.

No afirmar que se ha guardado si el servidor no lo ha confirmado. Probar
con un cambio de perfil inocuo y comprobarlo después de recargar.

### C. Probar el resto de los datos

El usuario indica que las demás funcionalidades probadas parecen
funcionar, pero no se ha documentado una prueba exhaustiva. Tras
corregir el perfil y la sesión, probar:

-   cargar el perfil;
-   guardar y recuperar una sesión de juego;
-   recuperar el historial;
-   otras funciones de datos que use la aplicación;
-   errores de conexión y respuestas HTTP no exitosas.

### D. Seguridad multiusuario

Antes de compartir públicamente la app:

-   revisar RLS en todas las tablas con datos de usuarios;
-   probar con dos cuentas de prueba que cada usuario solo accede a sus
    datos autorizados;
-   comprobar las funciones administrativas en el servidor, no solo en
    la interfaz;
-   nunca exponer claves secretas de Supabase en el paquete web;
-   no publicar el `.env`.

### E. Publicación e instalación de PWA

Se dejará para el final, como ha pedido el usuario. Cuando la
persistencia y el perfil estén corregidos:

1.  elegir y configurar un hosting estático;
2.  desplegar `dist`;
3.  configurar la URL pública y las redirecciones de autenticación en
    Supabase;
4.  probar el enlace en otro navegador/dispositivo;
5.  probar registro, confirmación de email, inicio de sesión y guardado
    de datos en el dominio público;
6.  instalar la PWA en móvil y comprobar icono, nombre, apertura y
    comportamiento online/offline.

------------------------------------------------------------------------

## 7. Forma de trabajar con el usuario

El usuario prefiere instrucciones en español, paso a paso, con
explicaciones claras desde cero. Para cambios de código, suele necesitar
bloques completos listos para copiar, y es mejor resolver un problema y
probarlo antes de continuar con otro.

**No hagas una refactorización amplia ni cambies varias partes a la
vez.** Primero pide los métodos `get_profile()` y `save_profile()` y el
fragmento de interfaz que los usa. Inspecciona los ficheros actuales,
pues este documento recoge fragmentos de una etapa anterior y puede no
coincidir exactamente con la versión presente en el disco.

No pidas al usuario que comparta `.env`, contraseñas, access tokens,
refresh tokens ni claves secretas. Si necesita mostrar una
configuración, debe ocultar los valores sensibles.

------------------------------------------------------------------------

## 8. Primer mensaje recomendado para retomar el trabajo

Puedes comenzar así:

> He leído el handoff anterior y la actualización. El inicio de sesión
> de la PWA ya funciona tras corregir el uso de `fetch()` en Pyodide con
> `to_js(options)`. Los dos problemas pendientes son la pérdida de
> sesión al recargar y que los cambios del perfil parecen no persistir.
> Antes de tocar nada, voy a revisar el código actual. Pásame el método
> `get_profile()`, el método `save_profile()` y el fragmento de la
> interfaz donde se llama a `save_profile()`. Vamos a diagnosticar el
> guardado del perfil primero y después abordaremos la persistencia de
> sesión, haciendo pruebas pequeñas entre cambios.
