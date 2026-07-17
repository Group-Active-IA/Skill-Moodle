"""Integración con la API de Active-IA (corrección automática con Gemini) para la Skill.

Portado del copiloto (`backend/moodle/active_ia.py`), con dos desacoples respecto del
original:

1. **Sin `config.py`**: las credenciales salen de ENV VARS (`ACTIVEIA_URL`,
   `ACTIVEIA_USER`, `ACTIVEIA_PASS`), igual que el resto de la Skill lee su config del
   entorno. NO hace falta `GEMINI_API_KEY`: la corrección la corre Active-IA del lado del
   servidor (nosotros solo disparamos y consultamos), así que Gemini nunca se toca por
   request desde acá.

2. **Sin browser / sin `parsers`**: para bajar el archivo del alumno de Moodle se usa el
   `MobileWSClient` de la Skill por API REST (mismo patrón que `ws_api.bajar_entrega`:
   `mod_assign_get_submissions` + `token_download`), en vez del `find_zip_url` +
   `client_moodle.download` que scrapeaba el HTML de calificación en el copiloto. El
   nombre del alumno lo da `core_user_get_users_by_field` (fullname), sin parsear HTML.

Autocontenido, mismo contrato que el resto del paquete: cada rama de error devuelve un
dict con "error"; NUNCA propaga una excepción hacia afuera. El flujo completo
(`corregir_con_active_ia`) baja el archivo del alumno, lo sube a Active-IA, dispara la
corrección, hace polling hasta que Gemini termina y, al final, DESCARGA LOCAL el PDF de
devolución.

Auth: JWT Bearer. El token se cachea en memoria y se re-loguea automáticamente ante un
401. Depende de `httpx` (ya está en requirements) + stdlib. La corrección la hace Gemini
del lado de Active-IA: es async y a veces DA TIMEOUT del servicio de IA (caso real y
frecuente); se maneja como error recuperable, no como excepción."""

import asyncio
import os

import httpx

from . import ws_api
from .almacen import SALIDAS_DIR
from .cliente import MobileWSClient

# ---------- Constantes ----------

# Estados que devuelve Active-IA para una entrega (confirmados por GET /entregas).
_ESTADO_OK = "CORREGIDA"
_ESTADO_ERROR = "ERROR"
# Cada cuántos segundos preguntamos si ya está corregida.
_POLL_INTERVAL_S = 5.0
# Timeout de red de las requests simples (login, GET). El poll tiene su propio reloj.
_HTTP_TIMEOUT_S = 30.0
# Timeout de la descarga del PDF de devolución (puede ser un archivo grande).
_PDF_TIMEOUT_S = 60.0


def _normalizar(texto: str) -> str:
    """Título normalizado para comparar (minúsculas, sin espacios de más)."""
    return " ".join(str(texto).lower().split())


# ---------- Cliente HTTP con auth cacheada ----------

class ActiveIAClient:
    """Cliente async de la API de Active-IA con token JWT cacheado en memoria.

    Re-loguea solo ante un 401 (token vencido). No abre un `AsyncClient`
    persistente: cada request usa uno efímero (`async with`), como el `MobileWSClient`
    de la Skill, para no arrastrar estado de conexión entre consultas del MCP."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None

    async def _login(self) -> None:
        """POST /auth/login → guarda el access_token en memoria. Lanza en caso de fallo
        (lo captura `request`, que lo convierte en dict de error)."""
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
            resp = await http.post(
                f"{self._base}/auth/login",
                json={"username": self._username, "password": self._password},
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def request(
        self, method: str, path: str, *, timeout: float = _HTTP_TIMEOUT_S, **kwargs
    ) -> httpx.Response:
        """Hace una request autenticada. Si no hay token, se loguea primero. Ante un
        401, re-loguea UNA vez y reintenta. Devuelve la Response cruda (el caller
        decide qué hacer con el status)."""
        if self._token is None:
            await self._login()
        url = f"{self._base}{path}"
        # follow_redirects=True: la API redirige 307 sin trailing slash y httpx, por
        # default, NO sigue el redirect (y en un POST perdería el body). Con esto lo
        # sigue preservando método y cuerpo.
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            resp = await http.request(method, url, headers=self._auth_headers(), **kwargs)
            if resp.status_code == 401:
                # Token vencido: re-login y un único reintento.
                await self._login()
                resp = await http.request(method, url, headers=self._auth_headers(), **kwargs)
            return resp


# Singleton del módulo, configurado desde ENV VARS (no `config.py`, a diferencia del
# copiloto). El default de la URL es el mismo que traía el copiloto en su config.
def _default_client() -> ActiveIAClient:
    return ActiveIAClient(
        base_url=os.environ.get("ACTIVEIA_URL", "https://api.active-ia.com/api/v1"),
        username=os.environ.get("ACTIVEIA_USER", ""),
        password=os.environ.get("ACTIVEIA_PASS", ""),
    )


_client: ActiveIAClient | None = None


def _get_client() -> ActiveIAClient:
    global _client
    if _client is None:
        _client = _default_client()
    return _client


# ---------- FUNCIÓN 1: pendientes / mapa Moodle↔Active-IA ----------

async def activeia_pendientes() -> dict:
    """GET /pendientes/moodle, devuelto LIMPIO para que el agente resuelva
    `comision_id`/`rubrica_id` a partir del `cmid` (assign_id) + `groupId` de Moodle.

    Forma real de la API: `{materias:[{id,nombre,unidades:[{cmid,titulo,subtitulo,
    comisiones:[{id,nombre,codigo,groupId,moodleGraderUrl,...}]}]}]}`. Devolvemos una
    versión aplanada por unidad, con la rúbrica inferida (por título) cuando se puede."""
    try:
        resp = await _get_client().request("GET", "/pendientes/moodle")
    except httpx.HTTPError as e:
        return {"error": f"No pude consultar /pendientes/moodle: {e}"}
    if resp.status_code != 200:
        return {"error": f"/pendientes/moodle devolvió {resp.status_code}", "body": resp.text[:300]}

    data = resp.json()
    materias_out: list[dict] = []
    for materia in data.get("materias", []):
        materia_id = materia.get("id")
        # Rúbricas de la materia una sola vez (para inferir rubrica_id por título).
        rubricas = await _rubricas_de_materia(materia_id)
        unidades_out: list[dict] = []
        for unidad in materia.get("unidades", []):
            titulo = unidad.get("titulo", "")
            rub = _match_rubrica(titulo, rubricas)
            unidades_out.append(
                {
                    "cmid": unidad.get("cmid"),  # = assign_id de Moodle
                    "titulo": titulo,
                    "subtitulo": unidad.get("subtitulo"),
                    "espera": unidad.get("espera"),
                    "corregidos": unidad.get("corregidos"),
                    "sin_entrega": unidad.get("sinEntrega"),
                    "rubrica_id": rub.get("id") if rub else None,
                    "comisiones": [
                        {
                            "comision_id": c.get("id"),
                            "nombre": c.get("nombre"),
                            "codigo": c.get("codigo"),
                            "group_id": c.get("groupId"),  # = group de Moodle
                            "espera": c.get("espera"),
                            "moodle_grader_url": c.get("moodleGraderUrl"),
                        }
                        for c in unidad.get("comisiones", [])
                    ],
                }
            )
        materias_out.append(
            {"materia_id": materia_id, "nombre": materia.get("nombre"), "unidades": unidades_out}
        )
    return {"ok": True, "materias": materias_out}


async def _rubricas_de_materia(materia_id: int | None) -> list[dict]:
    """Listado de rúbricas de una materia (GET /rubricas/?materia_id=...). Devuelve []
    ante cualquier problema — es un helper de inferencia, no debe romper el flujo."""
    if materia_id is None:
        return []
    try:
        resp = await _get_client().request(
            "GET", "/rubricas/", params={"materia_id": materia_id, "per_page": 100}
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("items", [])
    except httpx.HTTPError:
        return []


def _match_rubrica(unidad_titulo: str, rubricas: list[dict]) -> dict | None:
    """Cruza el título de la unidad de Moodle con el `titulo` de una rúbrica. La API usa
    el MISMO texto en ambos lados (confirmado), así que un match exacto normalizado es
    fiable; si hay varias con el mismo título (ej. parcial + su recuperatorio) devuelve
    la primera y el caller debería desambiguar."""
    objetivo = _normalizar(unidad_titulo)
    if not objetivo:
        return None
    for rub in rubricas:
        if _normalizar(rub.get("titulo", "")) == objetivo:
            return rub
    return None


# ---------- FUNCIÓN 2: resolver comision_id + rubrica_id desde Moodle ----------

async def activeia_resolver(assign_id: int | str, group_id: int) -> dict:
    """A partir del `cmid` (assign_id) + `groupId` de Moodle devuelve
    `{comision_id, rubrica_id, unidad_titulo}` cruzando /pendientes/moodle y /rubricas.

    Si no encuentra la unidad, el grupo o la rúbrica, devuelve un dict con "error"
    claro (nunca lanza)."""
    try:
        resp = await _get_client().request("GET", "/pendientes/moodle")
    except httpx.HTTPError as e:
        return {"error": f"No pude consultar /pendientes/moodle: {e}"}
    if resp.status_code != 200:
        return {"error": f"/pendientes/moodle devolvió {resp.status_code}"}

    cmid = str(assign_id)
    data = resp.json()
    for materia in data.get("materias", []):
        for unidad in materia.get("unidades", []):
            if str(unidad.get("cmid")) != cmid:
                continue
            # Encontramos la unidad: buscamos la comisión por groupId.
            comision = next(
                (c for c in unidad.get("comisiones", []) if c.get("groupId") == group_id), None
            )
            if comision is None:
                grupos = [c.get("groupId") for c in unidad.get("comisiones", [])]
                return {
                    "error": f"El groupId {group_id} no está en la tarea {cmid}. "
                    f"Grupos disponibles: {grupos}."
                }
            # Inferimos la rúbrica por título dentro de la materia.
            rubricas = await _rubricas_de_materia(materia.get("id"))
            rub = _match_rubrica(unidad.get("titulo", ""), rubricas)
            if rub is None:
                return {
                    "error": f"No pude inferir la rúbrica para '{unidad.get('titulo')}' "
                    f"(materia {materia.get('id')}). Pasá rubrica_id a mano.",
                    "comision_id": comision.get("id"),
                    "unidad_titulo": unidad.get("titulo"),
                }
            return {
                "ok": True,
                "comision_id": comision.get("id"),
                "rubrica_id": rub.get("id"),
                "unidad_titulo": unidad.get("titulo"),
                "materia_id": materia.get("id"),
                "moodle_grader_url": comision.get("moodleGraderUrl"),
            }
    return {"error": f"No encontré la tarea con cmid={cmid} en /pendientes/moodle."}


# ---------- Descarga del archivo del alumno de Moodle (por API REST, sin browser) ----------

async def _bajar_archivo_alumno(
    client_moodle: MobileWSClient, assign_id: str, email: str
) -> dict:
    """Baja la entrega del alumno de Moodle por API REST, reusando el MISMO patrón que
    `ws_api.bajar_entrega`: resuelve instanceid (`_instanceid`) + userid por email
    (`_userid_por_email`) → `mod_assign_get_submissions` → `token_download`. Reemplaza
    el `find_zip_url` + `client_moodle.download` (browser/scraping) del copiloto.

    Devuelve `{fname, data, alumno_nombre}` con los bytes del archivo principal, o un
    dict `{error}`. Nunca lanza (mismo contrato que el resto del módulo)."""
    # instanceid de la tarea (cmid -> assignment id): los WS de assign usan instanceid,
    # no cmid (lección aprendida del copiloto).
    try:
        inst = await ws_api._instanceid(client_moodle, assign_id)
    except Exception as e:  # noqa: BLE001 -- cualquier fallo de red/WS = dato, no excepción.
        return {"error": f"No pude resolver la tarea {assign_id}: {e}"}
    if inst is None:
        return {"error": f"No encontré la tarea con cmid={assign_id} (¿existe / es de tu curso?)."}

    # userid + fullname del alumno (el fullname nos sirve como alumno_nombre por default).
    try:
        uid, fullname = await ws_api._userid_por_email(client_moodle, email)
    except Exception as e:  # noqa: BLE001
        return {"error": f"No pude resolver al alumno {email}: {e}"}
    if uid is None:
        return {"error": f"No encontré al alumno {email} (¿entregó / está en el curso?)."}

    # Archivos entregados por ese uid (mod_assign_get_submissions).
    try:
        subs = await client_moodle.ws("mod_assign_get_submissions", {"assignmentids": [inst]})
    except Exception as e:  # noqa: BLE001
        return {"error": f"get_submissions falló para {assign_id}: {e}"}

    fileurl: str | None = None
    fname: str | None = None
    for asg in (subs or {}).get("assignments", []):
        for s in asg.get("submissions", []):
            if int(s.get("userid", -1)) != uid:
                continue
            for pl in s.get("plugins", []):
                for fa in pl.get("fileareas", []):
                    for f in fa.get("files", []):
                        if f.get("fileurl"):
                            # Nos quedamos con el primer archivo entregado (el principal):
                            # Active-IA recibe UN archivo por entrega.
                            fileurl = f["fileurl"]
                            fname = f.get("filename") or "entrega"
                            break
                    if fileurl:
                        break
                if fileurl:
                    break
            if fileurl:
                break
        if fileurl:
            break

    if not fileurl:
        return {"error": f"No encontré archivo entregado de {email} en la tarea {assign_id}."}

    # Descarga protegida (agrega el token del mobile a la pluginfile URL).
    try:
        data = await client_moodle.token_download(fileurl)
    except Exception as e:  # noqa: BLE001
        return {"error": f"No pude descargar la entrega de {email}: {e}"}

    return {"fname": fname or f"{assign_id}_{email}", "data": data, "alumno_nombre": fullname or email}


# ---------- FUNCIÓN 3: flujo completo de corrección ----------

async def corregir_con_active_ia(
    client_moodle: MobileWSClient,
    assign_id: str,
    email: str,
    comision_id: int,
    rubrica_id: int,
    alumno_nombre: str | None = None,
    moodle_url: str | None = None,
    timeout_s: int = 180,
    dest_dir: str | None = None,
) -> dict:
    """FLUJO COMPLETO de corrección con Active-IA. Toda rama devuelve dict (nunca lanza):

    a. Baja el archivo del alumno de Moodle por API REST (`_bajar_archivo_alumno`,
       mismo patrón que `ws_api.bajar_entrega` — sin browser).
    b. POST /entregas (multipart: archivo + alumno_nombre + comision_id + rubrica_id +
       moodle_url opcional). Si 409 → `{conflicto: True}` (no reintenta solo).
    c. POST /correcciones/entregas/{id}/corregir (dispara Gemini, async).
    d. Poll hasta CORREGIDA / ERROR o `timeout_s`. Si Active-IA da timeout del servicio
       de IA → `{error: "Active-IA timeout del servicio de IA, reintentá", estado}`.
    e. Si corrigió: DESCARGA LOCAL el PDF de devolución (`exportar_devolucion_pdf`) y lo
       agrega como `devolucion_pdf_local` (path en disco).
    f. Devuelve `{ok, nota, correccion_id, entrega_id, devolucion_pdf_url,
       devolucion_pdf_local, estado}`."""
    cli = _get_client()

    # --- a. Bajar el archivo del alumno de Moodle (API REST) ---
    archivo = await _bajar_archivo_alumno(client_moodle, assign_id, email)
    if "error" in archivo:
        return archivo
    fname = archivo["fname"]
    data = archivo["data"]
    # Si no vino alumno_nombre a mano, usamos el fullname que resolvió Moodle.
    if alumno_nombre is None:
        alumno_nombre = archivo["alumno_nombre"]

    # --- b. POST /entregas (multipart) ---
    entrega = await _subir_entrega(
        cli, fname, data, alumno_nombre, comision_id, rubrica_id, moodle_url
    )
    if "error" in entrega or entrega.get("conflicto"):
        return entrega
    entrega_id = entrega["entrega_id"]

    # --- c. Disparar la corrección ---
    disparo = await _disparar_correccion(cli, entrega_id)
    if "error" in disparo:
        # Un timeout al DISPARAR no es fatal: la corrección puede haber arrancado igual;
        # seguimos al polling. Cualquier otro error sí corta.
        if not disparo.get("timeout"):
            return {**disparo, "entrega_id": entrega_id}

    # --- d. Polling hasta corregida / error / timeout ---
    resultado = await _poll_correccion(cli, entrega_id, comision_id, timeout_s)

    # --- e. Descarga LOCAL del PDF de devolución (si hay correccion_id) ---
    correccion_id = resultado.get("correccion_id")
    if resultado.get("ok") and correccion_id is not None:
        pdf = await exportar_devolucion_pdf(cli, correccion_id, dest_dir)
        # Si la descarga del PDF falla, NO tumbamos la corrección (que ya salió bien):
        # devolvemos el path si se pudo, o un aviso si no.
        resultado["devolucion_pdf_local"] = pdf.get("path")
        if "error" in pdf:
            resultado["devolucion_pdf_aviso"] = pdf["error"]

    return resultado


# ---------- Descarga LOCAL del PDF de devolución ----------

async def exportar_devolucion_pdf(
    cli_activeia: ActiveIAClient, correccion_id: int, dest_dir: str | None = None
) -> dict:
    """Descarga LOCAL el PDF de devolución de una corrección de Active-IA.

    GET /documentos/correcciones/{correccion_id}/pdf con el JWT (mismo cliente que ya
    tiene el token cacheado) y guarda el archivo en `dest_dir`. Por default va al
    `salidas/` de la Skill (`$MOODLE_SKILL_HOME/salidas`, = `almacen.SALIDAS_DIR`), el
    mismo lugar donde `armar_informe` deja sus PDFs.

    Devuelve `{ok, path, bytes}` con la ruta absoluta del PDF descargado, o `{error}`
    (nunca lanza)."""
    destino = dest_dir or SALIDAS_DIR
    try:
        resp = await cli_activeia.request(
            "GET", f"/documentos/correcciones/{correccion_id}/pdf", timeout=_PDF_TIMEOUT_S
        )
    except httpx.HTTPError as e:
        return {"error": f"No pude descargar el PDF de devolución (correccion {correccion_id}): {e}"}
    if resp.status_code != 200:
        return {
            "error": f"GET /documentos/correcciones/{correccion_id}/pdf devolvió {resp.status_code}",
            "detalle": resp.text[:300],
        }

    try:
        os.makedirs(destino, exist_ok=True)
        path = os.path.join(destino, f"devolucion_{correccion_id}.pdf")
        with open(path, "wb") as fh:
            fh.write(resp.content)
    except OSError as e:
        return {"error": f"No pude guardar el PDF de devolución en {destino}: {e}"}

    return {"ok": True, "path": path, "bytes": len(resp.content)}


async def _subir_entrega(
    cli: ActiveIAClient,
    fname: str,
    data: bytes,
    alumno_nombre: str,
    comision_id: int,
    rubrica_id: int,
    moodle_url: str | None,
) -> dict:
    """POST /entregas multipart. Primero SIN modo de procesamiento (lo trae la rúbrica);
    si la API lo exige (422), reintenta con `modo_consolidacion="solo_codigo"`. Devuelve
    `{entrega_id}`, o `{conflicto: True}` ante 409, o `{error}`."""
    files = {"archivo": (fname, data, "application/zip")}
    form: dict[str, str] = {
        "alumno_nombre": alumno_nombre,
        "comision_id": str(comision_id),
        "rubrica_id": str(rubrica_id),
    }
    if moodle_url:
        form["moodle_url"] = moodle_url

    try:
        resp = await cli.request("POST", "/entregas/", data=form, files=files)
        # Si la API exige el modo de procesamiento, reintentamos con solo_codigo.
        if resp.status_code == 422 and "modo" in resp.text.lower():
            form["modo_consolidacion"] = "solo_codigo"
            resp = await cli.request("POST", "/entregas/", data=form, files=files)
    except httpx.HTTPError as e:
        return {"error": f"No pude subir la entrega a Active-IA: {e}"}

    if resp.status_code == 409:
        return {
            "conflicto": True,
            "aviso": f"Ya existe una entrega de '{alumno_nombre}' para esa rúbrica/comisión "
            "en Active-IA. Reintentá con sobrescribir=true si querés reemplazarla.",
            "detalle": resp.text[:300],
        }
    if resp.status_code not in (200, 201):
        return {"error": f"POST /entregas devolvió {resp.status_code}", "detalle": resp.text[:300]}

    try:
        entrega_id = resp.json()["id"]
    except (KeyError, ValueError):
        return {"error": "POST /entregas no devolvió un id de entrega.", "detalle": resp.text[:300]}
    return {"entrega_id": entrega_id}


async def _disparar_correccion(cli: ActiveIAClient, entrega_id: int) -> dict:
    """POST /correcciones/entregas/{id}/corregir. Un timeout de red acá NO es fatal (la
    corrección corre async del lado de Active-IA): se marca `timeout=True` para que el
    caller siga al polling en vez de abortar."""
    try:
        # Timeout generoso: el servicio a veces tarda en responder al disparo.
        resp = await cli.request(
            "POST", f"/correcciones/entregas/{entrega_id}/corregir", timeout=60.0
        )
    except httpx.TimeoutException:
        return {"error": "Timeout al disparar la corrección (sigo esperando).", "timeout": True}
    except httpx.HTTPError as e:
        return {"error": f"No pude disparar la corrección: {e}"}

    # 504/408 del gateway = el servicio tardó, pero la corrección puede seguir corriendo.
    if resp.status_code in (408, 504):
        return {"error": f"El servicio tardó ({resp.status_code}).", "timeout": True}
    if resp.status_code not in (200, 201, 202):
        return {
            "error": f"POST /corregir devolvió {resp.status_code}",
            "detalle": resp.text[:300],
        }
    return {"ok": True}


async def _poll_correccion(
    cli: ActiveIAClient, entrega_id: int, comision_id: int, timeout_s: int
) -> dict:
    """Poll de la corrección hasta que termina o vence `timeout_s`.

    Estrategia (GET /entregas/{id} está roto del lado del server → 500): consultamos
    GET /correcciones/entregas/{id} — 200 = corregida (trae nota + correccion_id), 404 =
    todavía no hay corrección. Ante 404 chequeamos la lista `?estado=ERROR` de la comisión
    para cazar fallos de Gemini (ej. GEMINI_OVERLOADED) sin esperar todo el timeout."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s

    while loop.time() < deadline:
        try:
            resp = await cli.request("GET", f"/correcciones/entregas/{entrega_id}")
        except httpx.HTTPError:
            # Error de red puntual: reintentamos en el próximo tick.
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue

        if resp.status_code == 200:
            corr = resp.json()
            correccion_id = corr.get("id")
            return {
                "ok": True,
                "estado": _ESTADO_OK,
                "entrega_id": entrega_id,
                "correccion_id": correccion_id,
                "nota": corr.get("nota"),
                "devolucion_pdf_url": (
                    f"{cli._base}/documentos/correcciones/{correccion_id}/pdf"
                    if correccion_id is not None
                    else None
                ),
            }

        if resp.status_code == 404:
            # Todavía no hay corrección: ¿falló Gemini? Miramos la lista de ERROR.
            err = await _buscar_error_entrega(cli, entrega_id, comision_id)
            if err is not None:
                return err
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue

        # Otro status inesperado: esperamos y reintentamos.
        await asyncio.sleep(_POLL_INTERVAL_S)

    return {
        "error": "Active-IA timeout del servicio de IA, reintentá",
        "estado": "pendiente",
        "entrega_id": entrega_id,
    }


async def _buscar_error_entrega(
    cli: ActiveIAClient, entrega_id: int, comision_id: int
) -> dict | None:
    """Busca la entrega en la lista de ERROR de la comisión. Si aparece, devuelve el dict
    de error con `error_code`/`error_mensaje` de Active-IA (ej. GEMINI_OVERLOADED). Si no
    está en ERROR, devuelve None (sigue procesando)."""
    try:
        resp = await cli.request(
            "GET",
            "/entregas/",
            params={"comision_id": comision_id, "estado": _ESTADO_ERROR, "per_page": 100},
        )
        if resp.status_code != 200:
            return None
        for item in resp.json().get("items", []):
            if item.get("id") == entrega_id:
                mensaje = item.get("error_mensaje") or "La corrección falló en Active-IA."
                return {
                    "error": mensaje,
                    "error_code": item.get("error_code"),
                    "estado": _ESTADO_ERROR,
                    "entrega_id": entrega_id,
                }
    except httpx.HTTPError:
        return None
    return None
