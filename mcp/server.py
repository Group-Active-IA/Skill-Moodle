"""MCP server "liviano de-un-tutor" para operar el campus TUP por API REST.

Un tutor lo corre LOCAL en su Claude Code con SUS credenciales de Moodle (env vars) y
opera su campus por la API REST oficial (token `moodle_mobile_app`). Reusa la lógica del
copiloto (`moodle.ws_api`, `moodle.informes`, `moodle.snapshot`) pero SIN nada del
multi-tenant: no hay SessionPool, ni `_tutor_actual`, ni `_validar_scope`, ni vault, ni
threading de `X-Tutor-Id`. Hay UN cliente global inicializado de env vars.

Config (env vars que el tutor setea):
    MOODLE_URL   base del campus (default https://tup.sied.utn.edu.ar)
    MOODLE_USER  usuario/DNI de login del tutor
    MOODLE_PASS  contraseña del tutor
    MOODLE_SKILL_HOME  (opcional) dir de datos locales (default ~/.moodle-skill)
    REFRESCO_TIMEOUT_S (opcional) techo de tiempo del snapshot on-demand (default 300)

Correr:  python server.py   (transport stdio: lo lanza el propio Claude Code)
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from moodle import (
    active_ia,
    almacen,
    auditoria,
    informes,
    panorama,
    snapshot,
    version,
    ws_api,
)
from moodle.cliente import MobileWSClient

log = logging.getLogger("skill.server")

mcp = FastMCP("moodle-tutor")

_BASE_DEFAULT = "https://tup.sied.utn.edu.ar"
# El .env vive junto a los datos locales (fuera del repo, nunca se versiona). Es la
# forma amigable de configurar: el tutor le dice sus credenciales a Claude y la tool
# `configurar` las escribe acá; no hace falta pelear con `export`.
_ENV_PATH = Path(almacen.HOME) / ".env"


def _cargar_env() -> None:
    """Puebla os.environ desde el .env local (KEY=valor por línea). El entorno real
    gana sobre el .env (setdefault): quien ya exportó una var, la mantiene."""
    if not _ENV_PATH.exists():
        return
    for linea in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            k, _, v = linea.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_cargar_env()
_REFRESCO_TIMEOUT_S = int(os.environ.get("REFRESCO_TIMEOUT_S", "300"))
_cliente: MobileWSClient | None = None


def _cli() -> MobileWSClient:
    """Cliente REST del tutor (singleton). Relee el .env por si se configuró en esta
    sesión (Claude corrió `configurar`). Falla claro si aún no hay credenciales."""
    global _cliente
    if _cliente is None:
        _cargar_env()
        base = os.environ.get("MOODLE_URL", _BASE_DEFAULT).rstrip("/")
        user = os.environ.get("MOODLE_USER")
        pw = os.environ.get("MOODLE_PASS")
        if not user or not pw:
            raise RuntimeError(
                "Todavía no configuraste tus credenciales. Decile a Claude tu usuario "
                "y contraseña de Moodle y pedile que llame a `configurar` — las guarda "
                f"en {_ENV_PATH}. (No hace falta setear env vars a mano.)"
            )
        _cliente = MobileWSClient(base, user, pw)
    return _cliente


def _escribir_env(vals: dict[str, str]) -> None:
    """Escribe/actualiza el .env local con permisos 600 (solo el tutor lo lee).
    Preserva las claves que ya estaban y no se pasan de nuevo."""
    existentes: dict[str, str] = {}
    if _ENV_PATH.exists():
        for l in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, _, v = l.partition("=")
                existentes[k.strip()] = v.strip()
    existentes.update({k: v for k, v in vals.items() if v})
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    cuerpo = "# Credenciales de la skill TUP Campus Navigator. NO subir a git.\n" + \
             "\n".join(f"{k}={v}" for k, v in existentes.items()) + "\n"
    _ENV_PATH.write_text(cuerpo, encoding="utf-8")
    os.chmod(_ENV_PATH, 0o600)
    for k, v in existentes.items():
        os.environ[k] = v  # disponibles ya en esta sesión


@mcp.tool()
async def configurar(
    moodle_user: str,
    moodle_pass: str,
    moodle_url: str = "",
    activeia_user: str = "",
    activeia_pass: str = "",
) -> dict:
    """Guardá las credenciales del tutor y dejá la skill lista para operar. Pedile al
    tutor su usuario y contraseña de Moodle (y, si va a usar Active-IA, esas también) y
    llamá esta tool: escribe un .env local (permisos 600, fuera del repo — la
    contraseña NO se versiona) y VALIDA el login contra el campus antes de darlo por
    bueno. Reemplaza el tener que setear variables de entorno a mano.

    Si el login falla, NO deja las credenciales como válidas: devuelve el error para
    que el tutor revise usuario/contraseña (ojo: el usuario de Moodle no siempre es el
    DNI)."""
    global _cliente
    vals = {"MOODLE_USER": moodle_user.strip(), "MOODLE_PASS": moodle_pass,
            "MOODLE_URL": (moodle_url or _BASE_DEFAULT).rstrip("/")}
    if activeia_user:
        vals["ACTIVEIA_USER"] = activeia_user.strip()
    if activeia_pass:
        vals["ACTIVEIA_PASS"] = activeia_pass
    _escribir_env(vals)
    _cliente = None  # forzar recreación con las credenciales nuevas
    # Validar contra el campus: un token + un descubrimiento liviano.
    try:
        cursos = await ws_api.descubrir_cursos(_cli())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Guardé las credenciales pero el login falló "
                f"({type(e).__name__}). Revisá usuario y contraseña (el usuario de "
                f"Moodle no siempre es el DNI). Detalle: {str(e)[:150]}"}
    return {"ok": True, "mensaje": f"Credenciales validadas y guardadas en {_ENV_PATH}. "
            f"Veo {len(cursos)} cursos tuyos. Ya podés mapear tus comisiones.",
            "cursos": len(cursos)}


# ---------- VERSIÓN Y ACTUALIZACIÓN ----------
@mcp.tool()
async def version_skill(forzar: bool = False) -> dict:
    """Versión instalada de la skill y si hay una nueva publicada en GitHub.

    La skill vive en un clon local en la máquina de cada tutor: sin esto, quien la
    instaló hace dos meses sigue con los bugs de hace dos meses sin manera de enterarse.
    El chequeo se cachea 24 h para no pegarle a GitHub en cada consulta (`forzar=true`
    lo saltea).

    `disponible` tiene TRES valores: true (hay una nueva), false (estás al día) y null
    (no se pudo averiguar, p. ej. sin red). Null NO significa que estés al día."""
    return await version.chequear(forzar=forzar)


@mcp.tool()
async def actualizar_skill() -> dict:
    """Actualiza la skill a la última versión publicada (`git pull --ff-only`).

    Si el tutor tiene cambios sin commitear NO toca nada y avisa: pisar trabajo ajeno es
    peor que quedarse desactualizado. Después de actualizar HAY QUE REINICIAR Claude Code
    — el MCP se carga al arrancar la sesión, así que hasta entonces sigue corriendo la
    versión vieja."""
    return await version.actualizar()


# ---------- MIS DATOS (config de la cohorte: la fuente de verdad de los IDs) ----------
@mcp.tool()
async def mis_datos() -> dict:
    """Configuración vigente del tutor ("Mis datos"): cursos, comisiones (group_id) y
    tareas (assign_id) mapeadas. CONSULTALA PRIMERO para resolver IDs en vez de asumir
    valores. Si viene vacía o la cohorte cambió, corré el descubrimiento (descubrir_cursos
    -> descubrir_comisiones -> listar_tareas), mostrale el mapeo al tutor y guardá con
    guardar_mis_datos."""
    await almacen.init_db()
    datos = await almacen.get_mis_datos()

    # Aviso de versión acá y no en una tool aparte: SKILL.md manda consultar `mis_datos`
    # primero, así que es el único lugar por el que todos los tutores pasan sí o sí. Va
    # cacheado 24 h y nunca rompe esta tool: si el chequeo falla, se sigue sin él.
    aviso_version = None
    try:
        v = await version.chequear()
        if v.get("disponible"):
            aviso_version = v["aviso"]
    except Exception as e:  # noqa: BLE001
        log.warning("Chequeo de versión falló: %s: %s", type(e).__name__, e)

    if not datos:
        salida = {
            "vacio": True,
            "aviso": "Sin datos guardados. Corré descubrir_cursos / descubrir_comisiones / "
                     "listar_tareas, confirmá el mapeo con el tutor y guardalo con guardar_mis_datos.",
        }
    else:
        salida = {"actualizado_at": await almacen.mis_datos_actualizada(), "datos": datos}
    if aviso_version:
        salida["actualizacion_disponible"] = aviso_version
    return salida


_AULAS_PATH = Path(__file__).parent / "aulas.json"


@mcp.tool()
async def aulas() -> dict:
    """Aulas (materia → curso) de la cohorte vigente, del catálogo, VALIDADAS contra los
    cursos reales del tutor. Usá esto PRIMERO al mapear: en vez de que el tutor descubra
    cursos, mostrale estas materias y que elija la suya. Devuelve las materias del catálogo
    que el tutor efectivamente tiene (course_id confirmado en su cuenta).

    Red de seguridad (la lección de no confiar en IDs fijos): si un course_id del catálogo
    ya NO existe en la cuenta del tutor, o el catálogo venció, se avisa y hay que caer a
    `descubrir_cursos` en vivo. NUNCA se mapea un aula que el tutor no tiene."""
    import datetime
    try:
        cat = json.loads(_AULAS_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"No pude leer el catálogo de aulas: {e}. Usá descubrir_cursos."}

    reales = {c["course_id"]: c["nombre"] for c in await ws_api.descubrir_cursos(_cli())}
    vigentes, faltantes = [], []
    for m in cat.get("materias", []):
        cid = m.get("course_id")
        if cid in reales:
            vigentes.append({"materia": m["materia"], "course_id": cid,
                             "nombre_campus": reales[cid]})
        else:
            faltantes.append(m)

    vencido = False
    vh = cat.get("vigente_hasta", "")
    try:
        vencido = datetime.date.today().strftime("%Y-%m") > vh
    except Exception:  # noqa: BLE001
        pass

    out = {"cohorte": cat.get("cohorte"), "materias": vigentes}
    if vencido:
        out["aviso"] = (f"El catálogo de aulas venció ({vh}). Toca el mantenimiento de "
                        "6 meses: actualizá mcp/aulas.json. Mientras, usá descubrir_cursos.")
    if faltantes:
        out["faltan_en_tu_cuenta"] = [m["materia"] for m in faltantes]
    if not vigentes:
        out["aviso"] = (out.get("aviso", "") + " Ninguna aula del catálogo está en tu "
                        "cuenta: mapeá con descubrir_cursos en vivo.").strip()
    return out


_COMISIONES_PATH = Path(__file__).parent / "comisiones.json"


def _clave_nombre(texto: str) -> str:
    """Normaliza un nombre para comparar: sin acentos, sin dobles espacios, minúsculas.
    Así 'Tomás García' matchea con 'tomas garcia' escrito a las apuradas."""
    import unicodedata
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto)
                         if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.lower().split())


@mcp.tool()
async def mi_comision(nombre: str) -> dict:
    """Resuelve, a partir del NOMBRE del tutor, qué comisión le toca en cada materia y
    qué actividades de cursada tiene para corregir (con su cmid). Es el atajo del mapeo:
    el tutor dice su nombre y no hace falta que descubra cursos, grupos ni tareas.

    Devuelve, por materia: course_id, comisión (nombre en el campus + group_id) y la
    lista de actividades de cursada (cierres de unidad + integradores/TPs). NO incluye
    parciales ni recuperatorios: esos tienen calendario propio, pedilos con listar_tareas.

    Matchea por nombre completo o por parte (apellido, nombre de pila), sin distinguir
    acentos ni mayúsculas. Si el nombre es ambiguo devuelve los candidatos para que el
    tutor elija — nunca elige por él.

    VALIDACIÓN (la regla de la skill: verificar en vivo, nunca inventar): cada group_id
    del catálogo se coteja contra los grupos reales del curso antes de devolverlo. Si el
    catálogo quedó viejo, lo dice y manda a descubrir_comisiones en vivo."""
    try:
        cat = json.loads(_COMISIONES_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"No pude leer el catálogo de comisiones: {e}. "
                         "Mapeá en vivo con descubrir_comisiones + listar_tareas."}

    buscado = _clave_nombre(nombre)
    if not buscado:
        return {"error": "Decime el nombre del tutor para buscar su comisión."}

    # Exacto primero; si no hay, por coincidencia parcial (apellido o nombre suelto).
    exactos, parciales = [], []
    for m in cat.get("materias", []):
        for c in m.get("comisiones", []):
            clave = _clave_nombre(c.get("tutor", ""))
            if not clave:
                continue
            fila = {"materia": m["materia"], "course_id": m["course_id"], **c}
            if clave == buscado:
                exactos.append(fila)
            elif buscado in clave or clave in buscado:
                parciales.append(fila)

    encontradas = exactos or parciales
    if not encontradas:
        tutores = sorted({c["tutor"] for m in cat.get("materias", [])
                          for c in m.get("comisiones", [])})
        return {"sin_resultado": True,
                "aviso": f"No encontré a '{nombre}' en el reparto de {cat.get('cohorte')}. "
                         "Puede que la comisión no esté asignada todavía, o que el nombre "
                         "esté escrito distinto en el catálogo.",
                "tutores_del_catalogo": tutores}

    # Si el parcial matcheó a más de una persona distinta, no adivinamos: preguntamos.
    distintos = {_clave_nombre(f["tutor"]) for f in encontradas}
    if not exactos and len(distintos) > 1:
        return {"ambiguo": True,
                "aviso": f"'{nombre}' matchea con más de un tutor. Decime cuál es.",
                "candidatos": sorted({f["tutor"] for f in encontradas})}

    # Validación en vivo: el group_id del catálogo tiene que existir en el curso real.
    asignaciones, desfasadas = [], []
    grupos_por_curso: dict[int, dict[int, str]] = {}
    for f in encontradas:
        cid = f["course_id"]
        if cid not in grupos_por_curso:
            try:
                gs = await ws_api.descubrir_comisiones(_cli(), cid)
                grupos_por_curso[cid] = {g["group_id"]: g["nombre"] for g in gs}
            except Exception:  # noqa: BLE001
                grupos_por_curso[cid] = {}
        reales = grupos_por_curso[cid]
        if reales and f["group_id"] not in reales:
            desfasadas.append({"materia": f["materia"], "comision": f["nombre_campus"],
                               "group_id_catalogo": f["group_id"]})
            continue
        materia = next(m for m in cat["materias"] if m["course_id"] == cid)
        asignaciones.append({
            "materia": f["materia"],
            "course_id": cid,
            "comision": f["comision"],
            "nombre_campus": reales.get(f["group_id"], f["nombre_campus"]),
            "group_id": f["group_id"],
            "actividades_cursada": materia.get("actividades_cursada", []),
        })

    out = {"tutor": encontradas[0]["tutor"], "cohorte": cat.get("cohorte"),
           "asignaciones": asignaciones}
    if desfasadas:
        out["aviso"] = ("Estas comisiones del catálogo ya no existen en el campus — quedó "
                        "viejo. Mapealas en vivo con descubrir_comisiones.")
        out["desfasadas"] = desfasadas
    return out


# ---------- MENSAJERÍA PRIVADA ----------

@mcp.tool()
async def mensajes_pendientes(limite: int = 50) -> dict:
    """Mensajes privados de alumnos que esperan respuesta del tutor: aquellos cuya última
    palabra la tuvo el alumno. Es el "qué me falta contestar" de la mensajería.

    Separa `sin_leer` (el tutor ni las abrió — lo más urgente) de `leidas_sin_responder`
    (las vio y quedaron colgadas). Cada una trae `conversacion_id`: abrila con
    `leer_conversacion` para ver el hilo antes de contestar."""
    return await ws_api.mensajes_pendientes(_cli(), limite)


@mcp.tool()
async def leer_mensajes(limite: int = 15) -> dict:
    """Conversaciones privadas recientes: con quién, cuántos sin leer, el último mensaje
    y quién lo escribió. Vista de bandeja. Para el hilo completo, `leer_conversacion`."""
    return {"conversaciones": await ws_api.leer_mensajes(_cli(), limite)}


@mcp.tool()
async def leer_conversacion(conversacion_id: int, limite: int = 30) -> dict:
    """Mensajes de una conversación en orden cronológico. LEELA ANTES DE CONTESTAR:
    `leer_mensajes` solo trae el último mensaje, y responder sin el hilo lleva a repetir
    lo ya dicho o a contestar otra cosa. El `conversacion_id` sale de `leer_mensajes` o
    de `mensajes_pendientes`."""
    return await ws_api.leer_conversacion(_cli(), conversacion_id, limite)


@mcp.tool()
async def responder_mensaje(alumno: str, texto: str, confirmado: bool = False) -> dict:
    """Manda un mensaje privado a un alumno. `alumno` puede ser su email (exacto) o parte
    de su nombre, que se busca entre las conversaciones del tutor.

    ESCRITURA — le llega al alumno y no se puede borrar. Llamala primero SIN `confirmado`:
    devuelve un preview con el texto. Mostráselo al tutor y recién con su OK explícito
    repetí con `confirmado=true`. Nunca mandes un mensaje sin ese OK.

    OJO: por nombre solo encuentra a quien YA tiene conversación con el tutor. Para
    escribirle por primera vez a alguien, pasá su email (lo conseguís con
    `buscar_alumno`)."""
    return await ws_api.responder_mensaje(_cli(), alumno, texto, confirmado)


# ---------- FOROS ----------

@mcp.tool()
async def foros_pendientes(course_id: int, group_ids: list[int] | None = None,
                           solo_consultas: bool = True, incluir_avisos: bool = False) -> dict:
    """Consultas de foro del curso que el tutor TODAVÍA NO respondió. Es el "qué me falta
    contestar", el equivalente en foros de `pendientes_por_corregir`.

    Devuelve dos listas separadas, porque no son la misma urgencia:
      - `sin_responder`: nadie contestó (0 réplicas). Trae `responder_a_post` y un extracto
        del texto, así podés encadenar directo con `responder_foro`.
      - `respondio_otro`: contestó alguien más (un compañero), pero vos no.

    "Respondida por vos" = hay un post tuyo en la discusión. No se infiere por rol: el WS
    de foros no devuelve roles y adivinar quién es docente sería inventar.

    FILTRO POR COMISIÓN: los foros son de todo el curso (Prog I tiene 27 comisiones), así
    que sin filtrar ves los hilos de los alumnos de los demás tutores. Si no pasás
    `group_ids`, se toman los de `mis_datos` para ese curso; si tampoco hay, se revisa el
    curso entero y se avisa.

    QUÉ FOROS MIRA: por defecto solo los de consultas/dudas. Los de avisos son de una vía
    (`incluir_avisos=true` para verlos) y el de "buscar dupla/compañero" es entre alumnos:
    tiene cientos de hilos que ningún tutor debe contestar. `solo_consultas=false` mira
    todos. La lista de lo salteado vuelve en `foros_salteados`, para que no haya recortes
    silenciosos."""
    if not group_ids:
        await almacen.init_db()
        datos = await almacen.get_mis_datos() or {}
        for c in datos.get("cursos", []):
            if c.get("course_id") == course_id:
                group_ids = [x["group_id"] for x in c.get("comisiones_del_tutor", [])]
                break
    return await ws_api.foros_pendientes(_cli(), course_id, group_ids, solo_consultas,
                                         incluir_avisos)


@mcp.tool()
async def listar_foros(course_id: int) -> dict:
    """Foros del curso con `forum_id`, `cmid`, nombre, tipo y cuántas discusiones tiene
    cada uno. OJO: `forum_id` es lo que pide `leer_foro`; `cmid` es el módulo en el aula.
    No son intercambiables. (API REST.)"""
    return await ws_api.listar_foros(_cli(), course_id)


@mcp.tool()
async def leer_foro(forum_id: int, limite: int = 25) -> dict:
    """Discusiones de un foro (título, autor, cuántas réplicas, si podés responder).
    El `forum_id` sale de `listar_foros`. Para leer los mensajes de una discusión,
    seguí con `leer_discusion`. (API REST.)"""
    return await ws_api.leer_foro(_cli(), forum_id, limite)


@mcp.tool()
async def leer_discusion(discussion_id: int) -> dict:
    """Mensajes de una discusión, en orden cronológico: el primero es la consulta original
    y después las respuestas. Cada post trae su `post_id` — ese es el que necesitás para
    contestar con `responder_foro`. (API REST.)"""
    return await ws_api.leer_discusion(_cli(), discussion_id)


@mcp.tool()
async def responder_foro(post_id: int, mensaje: str, asunto: str | None = None,
                         confirmado: bool = False) -> dict:
    """Publica una respuesta en un foro, colgando del post `post_id` (sale de
    `leer_discusion` o del `responder_a_post` que da `foros_pendientes`).

    ESCRITURA — va al campus y lo ven los alumnos. Llamala primero SIN `confirmado`:
    devuelve un preview. Mostráselo al tutor y recién con su OK explícito repetí la
    llamada con `confirmado=true`. Nunca publiques sin ese OK."""
    return await ws_api.responder_foro(_cli(), post_id, mensaje, asunto, confirmado)


@mcp.tool()
async def crear_discusion(forum_id: int, asunto: str, mensaje: str,
                          group_id: int | None = None, confirmado: bool = False) -> dict:
    """Abre un tema NUEVO en un foro (una bienvenida, un aviso). Distinto de
    `responder_foro`, que cuelga de un post que ya existe: usá esta cuando no hay hilo
    del que colgarse.

    EL `group_id` DECIDE QUIÉN LO VE. En los foros de "Avisos de la comisión" el aviso
    llega SOLO a esa comisión; con `group_id=0` se publica para el curso entero —
    cientos de alumnos ajenos, y **no se puede borrar desde la API**. Sacá el id de
    `mis_datos` o `descubrir_comisiones`, nunca inventado. Para avisos de comisión NO uses
    "Avisos generales": ese foro no tiene grupos y siempre va al curso completo.

    Si no pasás `group_id` en un foro con grupos, o si no se puede determinar el alcance,
    la tool **se niega a publicar** y te dice por qué. No insistas mandando `group_id=0`
    para saltar el error: ese valor significa "quiero que lo vea el curso entero" y hay
    que preguntárselo al tutor primero.

    ESCRITURA — llamala primero SIN `confirmado`: devuelve un preview que dice a qué
    grupo va y **a cuántos alumnos llega**, verificado en vivo. Mostráselo al tutor —
    sobre todo ese número — y recién con su OK explícito repetí con `confirmado=true`."""
    return await ws_api.crear_discusion(_cli(), forum_id, asunto, mensaje, group_id, confirmado)


@mcp.tool()
async def descubrir_cursos() -> list[dict]:
    """Descubre EN VIVO los cursos del campus donde el tutor está matriculado
    (course_id + nombre). Fallback de `aulas` si el catálogo no sirve. (API REST.)"""
    return await ws_api.descubrir_cursos(_cli())


@mcp.tool()
async def descubrir_comisiones(course_id: int) -> list[dict]:
    """Descubre EN VIVO los grupos de un curso (group_id + nombre). OJO: incluye grupos
    auxiliares que NO son comisiones (Grupo_2, Entrego_1er_examen…); quedate con los del
    patrón de comisión de tu cohorte (Prog I/II/III). Paso 2 del mapeo. Los group_id que devuelve son
    los ÚNICOS válidos para guardar_mis_datos (no inventes números). (API REST.)"""
    return await ws_api.descubrir_comisiones(_cli(), course_id)


@mcp.tool()
async def listar_tareas(course_id: int) -> list[dict]:
    """Lista las tareas del curso (TPs, parciales, integrador) con su cmid (=assign_id) y
    título. Paso 3 del mapeo de "Mis datos" (descubre los assign_id). (API REST.)"""
    return await ws_api.listar_tareas(_cli(), course_id)


@mcp.tool()
async def guardar_mis_datos(datos: dict) -> dict:
    """Guarda "Mis datos" (la config que usan el snapshot y los tableros). Estructura:
    {"tutor": {"nombre": str}, "cursos": [{"course_id": int, "nombre": str,
    "comisiones_del_tutor": [{"comision": str, "group_id": int}],
    "tareas": [{"assign_id": str, "titulo": str}]}]}.

    Es una acción de CONFIGURACIÓN: mostrale el mapeo al tutor y guardá recién tras su OK.

    VALIDACIÓN (lección aprendida): cada group_id se coteja EN VIVO contra
    descubrir_comisiones del curso — así el modelo no puede inventar group_ids. Si alguno
    no existe en el curso real, NO se guarda nada y se devuelve el detalle de los inválidos
    con la lista de grupos reales para que corrijas."""
    if not isinstance(datos, dict) or not datos.get("cursos"):
        return {"error": "Estructura inválida: se espera un dict con al menos 'cursos'."}

    # Cotejar cada group_id contra la lista REAL de grupos del curso (API REST).
    invalidos: list[dict] = []
    for curso in datos.get("cursos", []):
        cid = curso.get("course_id")
        if cid is None:
            continue
        reales = await ws_api.descubrir_comisiones(_cli(), int(cid))
        if reales and isinstance(reales[0], dict) and reales[0].get("error"):
            return {"error": f"No pude validar el curso {cid}: {reales[0]['error']}"}
        ids_reales = {g.get("group_id") for g in reales}
        for c in curso.get("comisiones_del_tutor", []):
            gid = c.get("group_id")
            if gid is not None and gid not in ids_reales:
                invalidos.append({
                    "course_id": cid, "comision": c.get("comision"), "group_id": gid,
                    "grupos_reales": sorted(g for g in ids_reales if g is not None),
                })
    if invalidos:
        return {
            "error": "Hay group_id que no existen en el curso real; no guardé nada.",
            "invalidos": invalidos,
            "aviso": "Corregí los group_id usando SOLO los que devuelve descubrir_comisiones.",
        }

    await almacen.set_mis_datos(datos)
    return {"ok": True, "cursos": len(datos.get("cursos", []))}


# ---------- REFRESCO DE TABLEROS (snapshot on-demand) ----------
@mcp.tool()
async def actualizar_tableros() -> dict:
    """Actualiza AHORA los tableros/caché corriendo el snapshot de TODOS los cursos
    mapeados en "Mis datos". LEE del campus (API REST) + ESCRIBE la caché LOCAL que usa
    buscar_alumno: NO toca el campus de Moodle.

    Cuándo: justo después de un guardar_mis_datos exitoso, o cuando el tutor pide
    'refrescá mis datos / fijate cómo vengo / verificá mis pendientes'.

    PUEDE TARDAR (varios requests): avisale al tutor antes. Devuelve un resumen
    (comisiones, entregas, alumnos, pendientes de corregir). Si todavía no mapeó
    comisiones, devuelve omitido=True. Si excede el techo de tiempo, timeout=True."""
    await almacen.init_db()
    try:
        res = await asyncio.wait_for(
            snapshot.tomar_snapshot(_cli()), timeout=_REFRESCO_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return {
            "error": True, "timeout": True,
            "mensaje": f"El refresco tardó más de {_REFRESCO_TIMEOUT_S}s y se cortó.",
            "que_quedo": "Se guarda por CURSO, no por tarea: desde que el relevamiento es "
                         "paralelo, las filas de un curso se escriben recién cuando "
                         "terminan TODAS sus tareas. Los cursos que alcanzaron a terminar "
                         "quedaron guardados enteros; el que estaba a mitad de camino "
                         "cuando se cortó no guardó nada. El padrón de alumnos y las "
                         "entregas se escriben al final de todo, así que ESOS siguen "
                         "mostrando la corrida anterior. No los leas como si fueran de hoy.",
            "que_hacer": "Reintentá: la segunda corrida suele entrar. Si vuelve a cortarse, "
                         "bajá la concurrencia con SNAPSHOT_CONCURRENCIA=3 (el campus puede "
                         "estar rechazando requests) o subí el techo con "
                         "REFRESCO_TIMEOUT_S=600. Y si sólo necesitás saber quién entregó "
                         "una tarea puntual, usá entregas_tarea, que responde en segundos.",
        }
    if res.get("omitido"):
        return {
            "ok": True, "omitido": True,
            "mensaje": "No hay comisiones mapeadas en 'Mis datos' todavía: corré el mapeo "
                       "(descubrir_cursos -> descubrir_comisiones -> listar_tareas -> "
                       "guardar_mis_datos) antes de refrescar los tableros.",
        }
    filas = await almacen.ultimo_snapshot()
    pendientes = sum(int(f.get("pendientes") or 0) for f in filas)
    comisiones = len({f.get("comision") for f in filas if f.get("comision")})
    return {
        "ok": True, "fecha": res.get("fecha"), "comisiones": comisiones,
        "tareas_relevadas": res.get("filas"), "entregas": res.get("entregas"),
        "alumnos": res.get("alumnos"), "pendientes_por_corregir": pendientes,
        "mensaje": f"Tableros actualizados: {comisiones} comisiones, "
                   f"{res.get('entregas')} entregas, {res.get('alumnos')} alumnos, "
                   f"{pendientes} pendientes de corregir.",
    }


# ---------- LECTURA ----------
@mcp.tool()
async def sumario(assign_id: str, group_id: int = 0) -> dict:
    """Conteo OFICIAL de Moodle de una tarea: participantes / enviados / pendientes por
    calificar. Es el número CONFIABLE para 'cuántos me faltan corregir'. group_id=0 = todo
    el curso; si no, el group_id de la comisión (el de descubrir_comisiones / "Mis datos",
    no un número inventado). Liviano: una request. (API REST.)"""
    return await ws_api.sumario(_cli(), assign_id, group_id)


@mcp.tool()
async def pendientes_por_corregir(assign_id: str, group_id: int = 0) -> dict:
    """Alumnos que ENTREGARON una tarea y siguen SIN NOTA. group_id=0 = todo el curso.

    Devuelve DOS motivos distintos, y el segundo no lo muestra ninguna otra vista:
      - `requiere_correccion`: la cola normal de corrección (`requiregrading`).
      - `calificado_sin_nota`: se guardó la devolución pero la calificación quedó vacía.
        Moodle los da por corregidos, así que salen de la cola SIN nota y nadie los
        espera. Si aparece alguno, hay que cargarle la nota a mano.
    (API REST: mod_assign_list_participants + mod_assign_get_grades.)"""
    return await ws_api.pendientes_tarea(_cli(), assign_id, group_id)


@mcp.tool()
async def entregas_tarea(assign_id: str, group_id: int = 0) -> dict:
    """Padrón COMPLETO de una tarea EN VIVO: TODOS los alumnos con nombre, email, estado
    ("Sin entrega" / "Enviado para calificar" / "Calificado") y nota. group_id=0 = todo el
    curso; si no, el group_id de la comisión (el de "Mis datos", no un número inventado).

    Usá ESTA cuando el tutor pregunte "quiénes entregaron / quiénes me deben la tarea X":
    son dos requests y responde en segundos. NO corras actualizar_tableros para eso — el
    snapshot recorre todas tus comisiones × todas tus tareas y tarda minutos.

    Devuelve `sin_entrega` aparte de `pendientes_por_corregir` a propósito: el que no
    entregó nada tiene 0 para corregir y NO está al día, es el que más debe. Lee la nota
    por texto de la escala (respeta el Aprobado/Desaprobado invertido). (API REST.)"""
    return await ws_api.entregas_tarea(_cli(), assign_id, group_id)


@mcp.tool()
async def buscar_alumno(texto: str, traza: bool = False) -> dict:
    """Busca un alumno por NOMBRE (o email) EN VIVO en las comisiones del tutor y devuelve
    quién es: comisión, email, userid y hace cuántos días que no entra al campus.

    NO necesita snapshot previo ni caché: una request por comisión (~1 s), siempre fresco.
    Insensible a mayúsculas y acentos. Si el nombre matchea a varias personas las devuelve
    todas, para que el tutor elija en vez de que se adivine.

    `traza=True` agrega qué entregó y qué nota sacó en CADA tarea del curso. Eso son dos
    requests por tarea, así que tarda bastante más: pedilo sólo cuando haga falta la
    situación académica completa, y sólo resuelve si la búsqueda dio UNA sola persona.
    (API REST: core_enrol_get_enrolled_users, filtrado a rol alumno.)"""
    await almacen.init_db()
    datos = await almacen.get_mis_datos()
    if not datos:
        return {
            "error": "No tengo tus cursos mapeados, así que no sé en qué comisiones buscar.",
            "siguiente_paso": "Corré mi_comision(tu nombre) y guardá con guardar_mis_datos.",
        }

    cursos = datos.get("cursos", [])
    res = await ws_api.buscar_alumnos(_cli(), texto, cursos)
    hallados = res.get("coincidencias") or []
    if not traza or not hallados:
        return res

    if len(hallados) > 1:
        # Misma disciplina que mi_comision: con varios candidatos no se adivina.
        res["aviso_traza"] = (
            f"'{texto}' matcheó a {len(hallados)} personas: no traigo la traza de todas. "
            "Repetí la búsqueda con el nombre completo o el email de la que te interesa."
        )
        return res

    alumno = hallados[0]
    tareas = next(
        (c.get("tareas", []) for c in cursos if c.get("course_id") == alumno.get("course_id")),
        [],
    )
    if not tareas:
        res["aviso_traza"] = (
            f"No tengo tareas mapeadas para {alumno.get('curso')}, así que no puedo armar "
            "la traza. Corré listar_tareas y sumalas con guardar_mis_datos."
        )
        return res

    alumno["traza"] = await ws_api.traza_alumno(_cli(), alumno, tareas)
    return res


@mcp.tool()
async def alumnos_en_riesgo(course_id: int | None = None, group_id: int = 0,
                            dias_alerta: int = 14, dias_aviso: int = 7) -> dict:
    """Quiénes están abandonando, ANTES de que se note. Cruza dos señales que ya estaban
    en el campus y que ninguna vista junta: hace cuántos días que no abre ESTA materia +
    cuántas tareas seguidas dejó de entregar.

    **Los días son SIN ABRIR ESTA MATERIA**, no sin entrar al campus. Son dos relojes
    distintos y hasta la v1.13.0 esto leía el equivocado: el alumno que entra todos los días
    para otra materia y nunca abrió la tuya daba 0 días → verde → y los verdes no se
    devuelven, así que era invisible. Cada fila trae ahora los dos relojes
    (`dias_sin_abrir_la_materia`, `dias_sin_entrar_al_campus`) más `estado_aula`.

    🔴 rojo: 2+ tareas SEGUIDAS sin entregar · o >`dias_aviso` días sin abrir la materia
       entrando al campus igual (**eligió no entrar**: la señal más fuerte) · o
       >`dias_alerta` días sin abrirla con al menos una sin entregar · o nunca la abrió y
       encima dejó de entregar o tampoco pisa el campus.
    🟡 amarillo: >`dias_aviso` días sin abrir la materia · o la última tarea sin entregar ·
       o nunca la abrió pero todavía no venció nada (puede haberse matriculado recién: NO lo
       presentes como abandono confirmado).
    ⚪ sin_datos: no se pudo leer su acceso a la materia. **No es verde**, es "no sabemos".
    (Los verdes no se devuelven: la lista es para actuar, no para leer 30 nombres.)

    Sin `course_id` toma el primero de "Mis datos"; con `group_id=0` recorre TODAS tus
    comisiones de ese curso. Va en vivo, sin depender del snapshot.

    La racha se cuenta desde la ÚLTIMA tarea, no sobre el total: quien no entregó el TP1
    hace tres meses pero viene entregando los últimos cinco no está abandonando; quien
    dejó de entregar los dos últimos, sí.

    OJO con la racha en cursos SIN fecha de entrega: si las actividades no tienen `duedate`
    (medido en Prog I: las 10 de cierre no la tienen), "sin entrega" no se puede distinguir
    de "todavía no vencía", y la racha marca a TODO el padrón — 94 de 94 alumnos en rojo. En
    ese caso la señal que sirve es el reloj de la materia, y conviene mirar
    `sin_entrar_al_aula`. Si ves el padrón entero en rojo por racha, decíselo al tutor en vez
    de presentarlo como que todos están abandonando."""
    await almacen.init_db()
    datos = await almacen.get_mis_datos()
    if not datos:
        return {"error": "No tengo tus cursos mapeados.",
                "siguiente_paso": "Corré mi_comision(tu nombre) y guardá con guardar_mis_datos."}

    cursos = datos.get("cursos", [])
    curso = (next((c for c in cursos if c.get("course_id") == course_id), None)
             if course_id else (cursos[0] if cursos else None))
    if curso is None:
        return {"error": f"No encontré el curso {course_id} en tus datos.",
                "cursos_disponibles": [c.get("course_id") for c in cursos]}

    # Sólo las actividades de cierre: el Integrador y los parciales tienen otra dinámica y
    # contarlos en la racha marcaba a media comisión sin que hubiera pasado nada.
    todas = curso.get("tareas", [])
    tareas = [t for t in todas if ws_api.es_actividad_de_cierre(t.get("titulo", ""))]
    excluidas = [t.get("titulo", "") for t in todas if t not in tareas]
    if not tareas:
        return {"error": "No encontré actividades de cierre de unidad en este curso.",
                "tareas_mapeadas": [t.get("titulo") for t in todas],
                "siguiente_paso": "Revisá el mapeo con listar_tareas / guardar_mis_datos."}

    grupos = ([{"comision": "(pedida)", "group_id": group_id}] if group_id
              else curso.get("comisiones_del_tutor", []))
    if not grupos:
        return {"error": f"No tenés comisiones mapeadas en {curso.get('nombre')}."}

    por_comision, avisos = [], []
    for g in grupos:
        r = await ws_api.alumnos_en_riesgo(
            _cli(), curso["course_id"], g["group_id"], tareas, dias_alerta, dias_aviso)
        if r.get("error"):
            avisos.append(f"{g['comision']}: {r['error']}")
            continue
        avisos.extend(r.get("_meta", {}).get("avisos", []))
        por_comision.append({"comision": g["comision"], **r})

    total_rojo = sum(c["rojo"] for c in por_comision)
    total_amarillo = sum(c["amarillo"] for c in por_comision)
    total_sin_datos = sum(c.get("sin_datos", 0) for c in por_comision)
    salida = {
        "ok": True,
        "curso": curso.get("nombre"),
        "course_id": curso["course_id"],
        "rojo": total_rojo,
        "amarillo": total_amarillo,
        "sin_datos": total_sin_datos,
        "comisiones": por_comision,
        "_meta": {
            "fuente": "vivo",
            "tareas_consideradas": [t.get("titulo") for t in tareas],
            "tareas_excluidas": excluidas,
            "nota_criterio": ("La racha se cuenta sólo sobre actividades de cierre de "
                              "unidad. El Integrador y los parciales quedan afuera: son "
                              "otra dinámica y contarlos marcaba a media comisión."),
            "degradado": bool(avisos),
            "avisos": avisos,
        },
    }
    con_alumnos = [c for c in por_comision if c.get("alumnos_totales")]
    if not con_alumnos:
        # Arranque de cuatrimestre: ninguna comisión tiene matriculados todavía.
        vacias = ", ".join(c["comision"] for c in por_comision) or "(ninguna)"
        salida["sin_alumnos"] = True
        salida["resumen"] = (f"Ninguna de tus comisiones ({vacias}) tiene alumnos "
                             "matriculados todavía. Eso NO es 'están todos al día': no hay "
                             "a quién evaluar. Volvé a correrlo cuando arranque la cursada.")
    elif not total_rojo and not total_amarillo and not total_sin_datos:
        n = sum(c["alumnos_totales"] for c in con_alumnos)
        salida["resumen"] = (f"Nadie en riesgo: los {n} alumnos vienen abriendo la materia y "
                             "entregando.")
    else:
        n = sum(c["alumnos_totales"] for c in con_alumnos)
        salida["resumen"] = (f"{total_rojo} en rojo y {total_amarillo} en amarillo. "
                             "Los rojos son los que hay que contactar esta semana.")
        if total_sin_datos:
            # Un `sin_datos` que no se nombra es un alumno que desaparece de la lista.
            salida["resumen"] += (f" Y {total_sin_datos} sin datos: de ésos no se pudo leer el "
                                  "acceso a la materia, no es que estén al día.")
        if total_rojo >= n:
            # El padrón entero en rojo no es información: es una alarma saturada. Suele pasar
            # cuando las actividades no tienen fecha de entrega y la racha cuenta como
            # abandono lo que todavía no vencía (medido en Prog I: 94 de 94).
            salida["alarma_saturada"] = True
            salida["resumen"] += (f" OJO: están los {n} alumnos en rojo, o sea TODOS. Eso no "
                                  "distingue a nadie. Revisá si las actividades tienen fecha "
                                  "de entrega: sin `duedate`, la racha cuenta como abandono lo "
                                  "que todavía no vencía. Para este caso mirá "
                                  "`sin_entrar_al_aula`, que no depende de vencimientos.")
    return salida


@mcp.tool()
async def sin_entrar_al_aula(course_id: int | None = None, group_id: int = 0,
                             dias_desenganche: int = 7) -> dict:
    """Quién dejó de abrir ESTA materia, ordenado por hace cuánto. Desenganche por materia.

    OJO con la diferencia, que es todo el punto de esta tool: `dias_sin_entrar` (de
    `buscar_alumno` y `alumnos_en_riesgo`) son días sin entrar al CAMPUS. Acá son días sin
    abrir ESTA materia. Son dos relojes distintos y el campus no avisa cuál estás mirando:
    el que entra todos los días para otra materia y nunca abre la tuya figura con
    `dias_sin_entrar: 0` — o sea, al día — estando desaparecido.

    Cada fila trae los DOS relojes y una frase (`detalle`). Leé el `detalle`, no el número
    pelado. El campo que decide a quién escribir es
    `entra_al_campus_sin_abrir_la_materia`: entró al campus en los últimos
    `dias_desenganche` días y hace `dias_desenganche`+ que no abre esta materia. Ése no
    perdió la contraseña, eligió no entrar. Presentá ésos primero.

    `estado_aula` tiene TRES valores y no se pueden confundir:
    - `abrio` → hay dato: `dias_sin_abrir_la_materia`.
    - `nunca_abrio` → nunca la abrió. NO lo presentes como abandono confirmado: puede
      haberse matriculado esta semana, y esto no ve la fecha de matriculación.
    - `sin_dato` → no se pudo leer. NO es "nunca abrió". Si aparece, el relevamiento está
      incompleto y hay que decirlo.

    El que hace 40 días que no abre la materia Y tampoco pisa el campus sale en la lista
    (arriba, por días) pero NO marcado para contactar: ése no eligió otra materia, no está
    en ninguna parte. Es otro problema y no se mezcla.

    Sin `course_id` toma el primero de "Mis datos"; con `group_id=0` recorre TODAS tus
    comisiones de ese curso. Va en vivo, una request por comisión.

    Por qué existe además de `alumnos_en_riesgo`: aquélla cuenta rachas de actividades sin
    entregar, y al principio del cuatrimestre —cuando no venció nada— marca al padrón entero.
    El reloj del curso sirve desde el día uno."""
    await almacen.init_db()
    datos = await almacen.get_mis_datos()
    if not datos:
        return {"error": "No tengo tus cursos mapeados.",
                "siguiente_paso": "Corré mi_comision(tu nombre) y guardá con guardar_mis_datos."}

    cursos = datos.get("cursos", [])
    curso = (next((c for c in cursos if c.get("course_id") == course_id), None)
             if course_id else (cursos[0] if cursos else None))
    if curso is None:
        return {"error": f"No encontré el curso {course_id} en tus datos.",
                "cursos_disponibles": [c.get("course_id") for c in cursos]}

    grupos = ([{"comision": "(pedida)", "group_id": group_id}] if group_id
              else curso.get("comisiones_del_tutor", []))
    if not grupos:
        return {"error": f"No tenés comisiones mapeadas en {curso.get('nombre')}."}

    por_comision, avisos = [], []
    for g in grupos:
        r = await ws_api.sin_entrar_al_aula(_cli(), curso["course_id"], g["group_id"],
                                            dias_desenganche)
        if r.get("error"):
            avisos.append(f"{g['comision']}: {r['error']}")
            continue
        avisos.extend(r.get("_meta", {}).get("avisos", []))
        por_comision.append({"comision": g["comision"], **r})

    if not por_comision:
        # Todas las comisiones fallaron: no hay nada que mostrar y hay que decirlo así,
        # nunca devolver una lista vacía que se lea como "no hay desenganchados".
        return {"error": "No pude relevar ninguna de tus comisiones, así que no sé quién "
                         "dejó de abrir la materia.",
                "curso": curso.get("nombre"), "course_id": curso["course_id"],
                "_meta": {"fuente": "vivo", "degradado": True, "avisos": avisos}}

    activos = sum(c["entran_al_campus_sin_abrir_la_materia"] for c in por_comision)
    desenganchados = sum(c["desenganchados"] for c in por_comision)
    nunca = sum(c["nunca_abrieron"] for c in por_comision)
    sin_dato = sum(c["sin_dato"] for c in por_comision)
    relevados = sum(c["alumnos_totales"] for c in por_comision)

    salida = {
        "ok": True,
        "curso": curso.get("nombre"),
        "course_id": curso["course_id"],
        "alumnos_relevados": relevados,
        "entran_al_campus_sin_abrir_la_materia": activos,
        "desenganchados": desenganchados,
        "nunca_abrieron": nunca,
        "sin_dato": sin_dato,
        "comisiones": por_comision,
        "_meta": {
            "fuente": "vivo",
            "dias_desenganche": dias_desenganche,
            "comisiones_relevadas": len(por_comision),
            "comisiones_pedidas": len(grupos),
            "nota_reloj": ("`dias_sin_abrir_la_materia` sale de `lastcourseaccess` (reloj "
                           "del curso). `dias_sin_entrar_al_campus` sale de `lastaccess` "
                           "(reloj del sitio). Moodle actualiza los dos con bandas muertas "
                           "de 60 s, así que diferencias de segundos son normales: la brecha "
                           "se cuenta en días enteros."),
            "degradado": bool(avisos) or len(por_comision) < len(grupos),
            "avisos": avisos,
        },
    }
    con_alumnos = [c for c in por_comision if c.get("alumnos_totales")]
    if not con_alumnos:
        vacias = ", ".join(c["comision"] for c in por_comision) or "(ninguna)"
        salida["sin_alumnos"] = True
        salida["resumen"] = (f"Ninguna de tus comisiones ({vacias}) tiene alumnos "
                             "matriculados todavía. Eso NO es 'están todos entrando': no hay "
                             "a quién medir.")
    elif activos:
        salida["resumen"] = (f"{activos} de {relevados} alumnos entran al campus pero hace "
                             f"{dias_desenganche}+ días que NO abren esta materia. Son los "
                             "que hay que contactar, y son los que `dias_sin_entrar` mostraba "
                             "como si estuvieran al día.")
    elif desenganchados:
        salida["resumen"] = (f"Nadie está entrando al campus sin abrir la materia. Quedan "
                             f"{desenganchados} desenganchados ({nunca} nunca la abrieron), "
                             "pero tampoco pisan el campus: es otro problema — o se les "
                             "cayó el acceso, o recién se matricularon.")
    else:
        # `relevados - sin_dato`: el que vino sin el dato no se relevó, y contarlo acá diría
        # "está todo al día" sobre alguien de quien no sabemos nada.
        salida["resumen"] = (f"Los {relevados - sin_dato} alumnos relevados abrieron la "
                             f"materia hace menos de {dias_desenganche} días. Nadie "
                             "desenganchado.")
        if sin_dato:
            salida["resumen"] += (f" OJO: {sin_dato} de {relevados} quedaron sin relevar: "
                                  "esto no cubre a todo el padrón.")
    if sin_dato:
        salida["aviso"] = (f"{sin_dato} alumno(s) vinieron sin el dato de último acceso a la "
                           "materia: quedaron como `sin_dato`, NO como 'nunca abrió'. El "
                           "relevamiento está incompleto.")
    return salida


@mcp.tool()
async def ver_entrega(assign_id: str, email: str, max_chars: int = 20000) -> dict:
    """Muestra QUÉ entregó un alumno: baja la entrega y devuelve su contenido.

    ES EL PASO PREVIO OBLIGATORIO A CALIFICAR A MANO. Sin esto sólo se podía cargar una
    nota sin haber visto el trabajo, que es exactamente lo que la regla de "verificar en
    vivo, nunca inventar" prohíbe — pero aplicada a lo que más importa: el legajo de una
    persona.

    Descomprime los .zip (la forma en que se entrega en la TUP) y devuelve el texto de los
    archivos de código. Los binarios (PDF, imágenes) se bajan igual y viene la `ruta` local
    para abrirlos aparte. `max_chars` reparte el presupuesto de texto entre los archivos.

    Read-only: no escribe nada en el campus. Para corregir con IA en vez de a mano está
    `corregir_con_active_ia` (usa la rúbrica oficial, si la unidad tiene una cargada).
    """
    destino = str(Path(almacen.SALIDAS_DIR) / "entregas" / str(assign_id))
    return await ws_api.leer_entrega(_cli(), assign_id, email, destino, max_chars)


# ---------- AUDITORÍA DE AULA (read-only, presencia/ausencia) ----------
@mcp.tool()
async def auditar_aula(course_id: int, materia: str = "", evaluador: str = "",
                       rol: str = "", con_navegador: bool = False,
                       unidad: int | None = None) -> dict:
    """Audita cómo está ARMADA un aula (no "quién entregó qué"): releva el curso por API
    REST, testea los links (rotos / piden login / a otro campus / con espacio), arma la
    matriz de unidades × 9 componentes en modo PRESENCIA/AUSENCIA y detecta hallazgos
    (componente faltante sistemático, hueco en un patrón, instancia extraordinaria visible,
    fechas de ciclos viejos). Escribe un worksheet .md en salidas/ y devuelve un resumen.

    PREGUNTÁ QUÉ UNIDAD antes de correr: un tutor audita SU unidad, no las 10. Ofrecé el
    número de unidad (1-10) o "todo el aula". Pasá `unidad=N` para auditar solo esa unidad
    (más rápido: testea solo sus links y cuestionarios). Sin `unidad`, releva el aula entera.
    Si la unidad no existe, devuelve `unidades_disponibles` para reintentar.

    REGLA (la misma de la skill): el agente verifica presencia/ausencia/consistencia, NO
    calidad. Puntaje 0=ausente, 3=presente, vacío=sin dato (no se infiere). La calidad y
    los puntajes finos los pone el evaluador humano sobre el borrador. La hoja EQUIPO se
    deja vacía a propósito: un agente no evalúa personas.

    `con_navegador=True` suma el PASO 2 (Playwright): loguea por navegador, cuenta las
    preguntas de cada cuestionario (mini=4 / autoeval=10 según la planilla) y clasifica las
    apps Google (NotebookLM/Colab) en abren / NO verificables (caen en login: eso no prueba
    que existan, un enlace borrado da la misma pantalla). Requiere Playwright instalado
    (`pip install playwright && playwright install chromium`); si no está, se saltea con
    aviso y la auditoría por API igual corre. Tarda más (abre una página por cuestionario).

    Es READ-ONLY sobre Moodle: no escribe nada en el campus, solo el worksheet local.
    `course_id` sale de `aulas` / `descubrir_cursos`. `materia` es el nombre para el
    encabezado (ej. 'Programación 2'); `evaluador`/`rol` son opcionales (dejá `evaluador`
    vacío para firmar como Celda de Control de Calidad)."""
    return await auditoria.auditar_aula(
        _cli(), course_id, almacen.SALIDAS_DIR, materia=materia, evaluador=evaluador,
        rol=rol, con_navegador=con_navegador, unidad=unidad)


# ---------- VISTA DEL PROFESOR (todas las comisiones a la vez) ----------

async def _cmids_del_curso(course_id: int, cmids: list[str] | None) -> list[str]:
    """Tareas a mirar: las que se pidieron, o TODAS las del curso descubiertas en vivo.

    En vivo y no de "Mis datos" a propósito: el profesor mira un curso entero, y el mapeo
    local es el de SUS comisiones como tutor — usarlo acá le escondería tareas del curso
    que él no tiene mapeadas. `listar_tareas` sale del `_assign_map`, que es una request.
    """
    if cmids:
        return [str(c) for c in cmids]
    return [str(t["id"]) for t in await ws_api.listar_tareas(_cli(), course_id)]


@mcp.tool()
async def panorama_comisiones(course_id: int, cmids: list[str] | None = None,
                              incluir_foros: bool = True) -> dict:
    """LA VISTA DEL PROFESOR: una fila por comisión del curso — tutor a cargo, alumnos,
    entregas, cuántas siguen sin corregir, hace cuántos días espera la más vieja y cuántas
    consultas de foro no contestó nadie. Responde "¿dónde hay trabajo parado, y a quién
    llamo?" sin abrir las 16 comisiones a mano.

    Es distinto de todo el resto de la skill, que mira UNA comisión (la del tutor logueado).
    Requiere ver comisiones ajenas: si tu rol en el campus no lo permite, las filas vuelven
    con `sin_dato` explicando el motivo — nunca con un 0.

    CÓMO SE LEE, y esto no es opcional: **las filas son HECHOS, no una evaluación del
    tutor.** Nombrar al docente de una comisión es un dato de ruteo (a quién llamar), no un
    juicio. Un número alto puede ser una comisión más grande, una consigna más difícil o una
    semana de parcial. Antes de cualquier conclusión, mirá `sin_dato` de esa fila: distingue
    "0 porque está al día" de "0 porque la comisión está vacía", "porque nadie entregó
    todavía" o "porque no pude leer". Un 0 con motivo NO es trabajo terminado.
    NO ordenes las filas como ranking ni las presentes como puntaje de nadie.

    `cmids` acota a ciertas tareas; sin pasarlo mira TODAS las del curso (más lento).
    `incluir_foros=False` saltea los foros, que son la parte más cara.
    Es READ-ONLY: no escribe nada en el campus."""
    return await panorama.panorama_comisiones(
        _cli(), course_id, await _cmids_del_curso(course_id, cmids), incluir_foros)


@mcp.tool()
async def demora_correccion(course_id: int, cmids: list[str] | None = None) -> dict:
    """Cuánto ESPERA un alumno desde que entrega hasta que le cargan la nota, por comisión.
    Es la pregunta que el conteo de pendientes no contesta: 20 entregas de ayer están bien,
    3 esperando hace tres semanas están mal. El conteo no las distingue; esto sí.

    Devuelve dos bloques que no hay que mezclar:
      - `demora_*`  → sobre entregas YA corregidas. Es historia.
      - `espera_*`  → sobre las que siguen sin corregir, contra hoy. Es lo accionable.

    Misma regla de lectura que `panorama_comisiones`: son hechos por comisión, no un puntaje
    del tutor. `sin_dato` avisa cuándo no hay nada medible — que no es lo mismo que estar
    al día. Sin `cmids` mira todas las tareas del curso. READ-ONLY."""
    return await panorama.demora_correccion(
        _cli(), course_id, await _cmids_del_curso(course_id, cmids))


@mcp.tool()
async def informe_profesor(course_id: int, cmids: list[str] | None = None,
                           dias_desenganche: int = 7, incluir_foros: bool = True,
                           pdf: bool = True, emails: bool = True) -> dict:
    """EL informe del curso para el profesor/coordinador, en una sola pasada: el trabajo de
    corrección por comisión **+ los alumnos que dejaron de abrir la materia**. Con `pdf=True`
    (por defecto) escribe además el **PDF de 3 páginas** listo para mandar a coordinación, y
    devuelve la ruta en `pdf.archivo`. Con `pdf=False` sólo los datos.

    Reemplaza los scripts sueltos con los que la coordinación venía armando estos informes.
    Eso importa más de lo que parece: cada script ad-hoc vuelve a aprender de cero las trampas
    del campus, y este campus tiene varias que ya costaron bugs reales — que las entregas vienen
    infladas con las que el alumno abrió y nunca envió, que Moodle guarda un `-1` en la nota de
    lo que TODAVÍA no se corrigió, que el `groupid` de una entrega viene 0 y no significa "grupo
    0", que existe el estado "calificado sin nota". Acá eso ya está resuelto y verificado contra
    el conteo oficial de Moodle.

    Junta las dos mitades que ninguna vista del campus cruza. `panorama_comisiones` y
    `demora_correccion` miden lo que deben los TUTORES; esto agrega lo que decide si un alumno
    abandona: quién dejó de aparecer por la materia. El padrón se baja una sola vez, así que la
    mitad nueva no cuesta requests extra.

    **Cómo presentarlo (importante).** La tool NO emite veredicto y vos tampoco: no escribas
    "estado general: sano" ni equivalentes. Un informe real de coordinación abría con "sano" y
    "el único foco son 7 alumnos" sobre 238, porque cortaba el desenganche por el reloj del
    CAMPUS — criterio que sobre datos medidos pierde el ~90% de los casos. Presentá los hechos,
    leé `_meta.sin_dato` ANTES de los números y decí los huecos. La conclusión es del profesor.

    Dos reglas duras que no se negocian al mostrarlo:
    - **Se audita el TRABAJO, nunca se califica a la PERSONA.** Nombrar al tutor de una comisión
      es ruteo (a quién llamar) y va; un ranking o un puntaje de tutores NO va, ni en la tabla
      ni en cómo lo contás.
    - **El desenganche es sobre ALUMNOS y se mide con el reloj de la MATERIA.** Cada fila trae
      `dias_sin_abrir_la_materia` y `dias_sin_entrar_al_campus`: son distintos y no se
      intercambian. Mirá `detalle`, que ya dice cuál es el caso de cada uno.

    En `desenganche.alumnos` van primero los que **entran al campus y no abren la materia**:
    ésos no perdieron el acceso, eligieron no entrar — es el grupo más accionable y el más
    recuperable. Después los que no aparecen por ningún lado, que son otro problema.

    `estado_aula`: `abrio` · `nunca_abrio` (NO es abandono confirmado: puede haberse matriculado
    esta semana) · `sin_dato` (no se pudo leer, **no** digas que no la abrió).

    El PDF trae la Regional de cada alumno (sale de sus grupos `R-*`, no del perfil) y un corte
    por regional, para ver si el desenganche se concentra en una sede.

    `emails=True` (por defecto) incluye el mail de cada alumno de la lista de riesgo, que es lo
    que hace el documento accionable — se le puede escribir sin volver a buscar a nadie. Son
    mails **personales**, así que **cuando le pases la ruta al tutor decile que ese PDF no va a
    un repo ni a una nota compartida**; el propio documento lo avisa en el pie. `emails=False`
    genera la versión sin datos de contacto, para cuando el informe circula más lejos.

    Sin `cmids` mira todas las tareas del curso. READ-ONLY sobre el campus: lo único que escribe
    es el PDF, local, en `salidas/`."""
    from datetime import date

    datos = await panorama.informe_profesor(
        _cli(), course_id, await _cmids_del_curso(course_id, cmids),
        dias_desenganche, incluir_foros)
    if datos.get("error") or not pdf:
        return datos
    try:
        datos["pdf"] = informes.informe_profesor_pdf(
            datos, str(Path(almacen.SALIDAS_DIR) / "informes"),
            materia=datos.get("curso") or "", fecha=date.today().isoformat(),
            emails=emails)
    except Exception as e:
        # Que falle el render NO puede tirar los datos: se relevó el curso entero y eso vale.
        datos["pdf"] = {"error": f"No pude escribir el PDF: {type(e).__name__}: {e}"}
        datos["_meta"]["degradado"] = True
    return datos


# ---------- INFORME (PDF) ----------
@mcp.tool()
async def armar_informe(course_id: int | None = None, group_id: int = 0) -> dict:
    """Genera un PDF de correcciones pendientes del curso (API REST + reportlab). Si no
    pasás course_id, usa el primer curso de tus "Mis datos". group_id=0 = todo el curso.
    Devuelve la ruta del PDF generado."""
    await almacen.init_db()
    cid = course_id
    if cid is None:
        datos = await almacen.get_mis_datos()
        for cu in (datos or {}).get("cursos", []):
            if cu.get("course_id") is not None:
                cid = int(cu["course_id"])
                break
    if not cid:
        return {"error": True, "mensaje": "No sé de qué curso armar el informe: pasá "
                "course_id o mapeá tus datos primero (descubrir_cursos -> guardar_mis_datos)."}
    return await informes.informe_pendientes(_cli(), cid, almacen.SALIDAS_DIR, group_id=group_id)


# ---------- CORRECCIÓN AUTOMÁTICA (Active-IA / Gemini) ----------
@mcp.tool()
async def activeia_pendientes() -> dict:
    """Mapa Moodle<->Active-IA: materias->unidades (con `cmid`=assign_id de Moodle y la
    `rubrica_id` inferida por título)->comisiones (con `comision_id` de Active-IA y
    `group_id` de Moodle). Sirve para resolver a mano comision_id/rubrica_id antes de
    corregir.

    ⚠️ Sus contadores `espera`/`corregidos` son del ESTADO EN MOODLE, no de Active-IA:
    `corregidos: 0` quiere decir "sin nota cargada en el campus", NO "Active-IA no corrigió".
    Para saber qué corrigió Active-IA y con qué nota, usá `activeia_correcciones`."""
    return await active_ia.activeia_pendientes()


@mcp.tool()
async def activeia_correcciones(comision_id: int, solo_corregidas: bool = True) -> dict:
    """QUÉ CORRIGIÓ Active-IA y con qué nota, por comisión. La vista que `activeia_pendientes`
    NO da (esa lee el estado de Moodle).

    Usala sobre todo después de un error `GEMINI_OVERLOADED`: ese error significa que la
    respuesta no llegó a tiempo, NO que la corrección se perdió — muchas terminan bien
    minutos después. Antes de reintentar o de corregir a mano, mirá acá.

    El `comision_id` sale de `activeia_resolver`. Devuelve `{comision_id, total,
    correcciones:[{entrega_id, alumno, estado, nota, correccion_id, rubrica_id}]}`.
    Que una entrega figure con nota acá NO significa que esté cargada en el campus: para
    eso está `cargar_nota`, que es un paso aparte."""
    return await active_ia.activeia_correcciones(comision_id, solo_corregidas)


@mcp.tool()
async def activeia_resolver(assign_id: str, group_id: int) -> dict:
    """A partir del `cmid` (assign_id) + `group_id` de Moodle devuelve
    `{comision_id, rubrica_id, unidad_titulo, moodle_grader_url}` cruzando
    /pendientes/moodle y /rubricas de Active-IA. Es el paso previo a
    `corregir_con_active_ia`. Si no puede inferir la rúbrica, devuelve el comision_id
    igual y avisa que pases rubrica_id a mano. (API REST de Active-IA.)"""
    return await active_ia.activeia_resolver(assign_id, group_id)


@mcp.tool()
async def corregir_con_active_ia(
    assign_id: str,
    email: str,
    comision_id: int,
    rubrica_id: int,
    alumno_nombre: str | None = None,
    moodle_url: str | None = None,
    timeout_s: int = 180,
    confirmado: bool = False,
) -> dict:
    """Corrige la entrega de un alumno con Active-IA (Gemini) de punta a punta: baja el
    archivo de Moodle (API REST, sin navegador), lo sube a Active-IA, dispara la
    corrección, espera el resultado y DESCARGA LOCAL el PDF de devolución.

    NO carga la nota en Moodle. Deja la nota sugerida y el PDF de devolución bajado a
    disco; escribir en el campus es un paso APARTE con `cargar_nota`, que tiene su propia
    confirmación. Aun así es una ESCRITURA (crea la entrega y la corrección en Active-IA):
    llamá primero con confirmado=false para previsualizar; recién tras el OK del tutor,
    confirmado=true. Antes conseguí comision_id/rubrica_id con
    `activeia_resolver(assign_id, group_id)`.

    Devuelve `{ok, nota, correccion_id, entrega_id, devolucion_pdf_url,
    devolucion_pdf_local, estado}`. `devolucion_pdf_local` es la ruta del PDF de
    devolución bajado a `$MOODLE_SKILL_HOME/salidas`. Casos que devuelve como dict (no
    rompe): `conflicto=True` si ya existe la entrega; `error` con "timeout del servicio
    de IA" si Gemini se satura (reintentá más tarde)."""
    if not confirmado:
        return {
            "preview": {
                "accion": "corregir_con_active_ia",
                "alumno": alumno_nombre or email,
                "email": email,
                "assign_id": assign_id,
                "comision_id": comision_id,
                "rubrica_id": rubrica_id,
            },
            "aviso": "Esto baja la entrega y la corrige con Active-IA (Gemini), y deja el "
                     "PDF de devolución en disco. NO escribe la nota en Moodle: para eso "
                     "hace falta después cargar_nota, que se confirma aparte. Revisalo y "
                     "volvé a llamar con confirmado=true para ejecutar.",
        }
    return await active_ia.corregir_con_active_ia(
        _cli(), assign_id, email, comision_id, rubrica_id,
        alumno_nombre=alumno_nombre, moodle_url=moodle_url, timeout_s=timeout_s,
    )


# ---------- ESCRITURA (con confirmación) ----------
@mcp.tool()
async def cargar_nota(assign_id: str, email: str, nota: str, mensaje: str,
                      confirmado: bool = False, etiquetas: list[str] | None = None,
                      adjunto: str | None = None) -> dict:
    """Escribe nota + devolución en Moodle. Llamá primero con confirmado=false para
    previsualizar; recién tras el OK del tutor, confirmado=true.

    La nota depende del TIPO de calificación de la tarea:
    - **TPs (ESCALA)**: pasá el texto EXACTO 'Aprobado' o 'Desaprobado' (NO un número).
      Si no coincide, devuelve es_escala=true + la lista de opciones válidas.
    - **Integrador (TIO) / numéricas**: pasá el número (coma decimal, ej. '9,85').
    Devuelve `verificado` (relee la nota por API para confirmar el guardado). (API REST:
    mod_assign_save_grade — sin navegador.)

    `etiquetas`: los TEMAS que se le marcaron al alumno, en kebab-case y reutilizables
    entre alumnos (ej. ["perimetro-circulo", "conversion-unidades", "operador-mayor-igual"]).
    Poné una por error real corregido; si el trabajo estaba impecable, dejalas vacías.
    Se guardan en la bitácora local y son lo que después alimenta `errores_frecuentes`:
    sin ellas, ese dato NO se puede reconstruir después. Usá el MISMO nombre de tema para
    el mismo error en distintos alumnos — ahí está toda la gracia.

    `adjunto`: ruta local de un archivo para ADJUNTAR a la devolución (típicamente el PDF
    que dejó `corregir_con_active_ia` en `salidas/`). Sin esto la devolución es sólo texto:
    si el mensaje dice "te adjunto el PDF" y no se pasa `adjunto`, el alumno lee que hay un
    archivo que no existe. Si la subida falla, la nota se carga igual y volvés
    `adjunto_aviso` explicando por qué no se adjuntó."""
    res = await ws_api.cargar_nota(_cli(), assign_id, email, nota, mensaje, confirmado,
                                   adjunto=adjunto)

    # Bitácora: sólo si la nota efectivamente quedó escrita. Registrar un preview o una
    # escritura fallida contaminaría las estadísticas con correcciones que no existieron.
    if confirmado and res.get("ok"):
        await _registrar_bitacora(res, assign_id, email, mensaje, etiquetas)
    return res


async def _contexto_tarea(assign_id: str) -> tuple:
    """(course_id, titulo, comision) de una tarea, desde "Mis datos". Todo None si no está."""
    datos = await almacen.get_mis_datos() or {}
    for c in datos.get("cursos", []):
        for t in c.get("tareas", []):
            if str(t.get("assign_id")) == str(assign_id):
                coms = c.get("comisiones_del_tutor", [])
                return (c.get("course_id"), t.get("titulo"),
                        coms[0].get("comision") if len(coms) == 1 else None)
    return (None, None, None)


async def _registrar_bitacora(res: dict, assign_id: str, email: str, mensaje: str,
                              etiquetas: list | None, comision: str | None = None) -> None:
    """Deja la corrección en la bitácora. NUNCA rompe la carga: si falla, la nota ya está
    escrita en Moodle y lo único que se pierde es la estadística, así que se avisa y sigue."""
    try:
        await almacen.init_db()
        curso, tarea, com = await _contexto_tarea(assign_id)
        await almacen.guardar_correccion({
            "course_id": curso, "assign_id": assign_id, "tarea": tarea,
            "comision": comision or com, "email": email, "alumno": res.get("alumno"),
            "nota": res.get("nota"), "devolucion": mensaje, "etiquetas": etiquetas or [],
        })
        res["registrado_en_bitacora"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("No pude registrar la corrección: %s: %s", type(e).__name__, e)
        res["registrado_en_bitacora"] = False
        res["aviso_bitacora"] = ("La nota se cargó bien, pero no se pudo registrar en la "
                                 "bitácora local: este caso no va a figurar en "
                                 "errores_frecuentes.")


# ---------- SESIÓN DE CORRECCIÓN EN LOTE ----------
@mcp.tool()
async def preparar_correccion(assign_id: str, group_id: int,
                              reemplazar: bool = False) -> dict:
    """Arma la cola para corregir una tarea entera de una comisión, de a un alumno por vez.

    Corregir 15 TPs de a uno son 15 idas y vueltas completas. Con la cola vas resolviendo
    alumno por alumno SIN tocar Moodle, y al final `confirmar_cola` escribe todo junto con
    una sola confirmación — pero mostrándote antes las 15 notas juntas, así el OK es
    informado y no a ciegas.

    La cola es PERSISTENTE: si cortás a la mitad, al volver seguís donde estabas y lo ya
    anotado no se pierde. `reemplazar=true` la descarta y arranca de nuevo.

    Se encolan sólo los que entregaron y no tienen nota (incluidos los "calificados sin
    nota", que no salen en ninguna otra cola)."""
    await almacen.init_db()
    pend = await ws_api.pendientes_tarea(_cli(), assign_id, group_id)
    if pend.get("error"):
        return pend
    alumnos = [{"email": a.get("email"), "nombre": a.get("name")}
               for a in pend.get("alumnos", [])]
    if not alumnos:
        return {"ok": True, "en_cola": 0,
                "aviso": "No hay entregas pendientes de corrección en esta tarea/comisión."}
    _, titulo, comision = await _contexto_tarea(assign_id)
    r = await almacen.cola_abrir(assign_id, titulo, group_id, comision, alumnos, reemplazar)
    return {"ok": True, "assign_id": str(assign_id), "group_id": group_id, "tarea": titulo,
            **r,
            "siguiente_paso": "Llamá `siguiente_para_corregir` para arrancar. Nada se "
                              "escribe en Moodle hasta `confirmar_cola`."}


@mcp.tool()
async def siguiente_para_corregir(assign_id: str | None = None,
                                  group_id: int | None = None,
                                  max_chars: int = 20000) -> dict:
    """El próximo alumno de la cola, CON su entrega ya bajada y lista para leer.

    Devuelve el contenido del trabajo para que se pueda corregir sin pasos intermedios.
    Después de decidir, se anota con `anotar_correccion` y se vuelve a llamar a esta."""
    await almacen.init_db()
    fila = await almacen.cola_siguiente(assign_id, group_id)
    if not fila:
        restan = await almacen.cola_listar(assign_id, group_id, estados=("anotado",))
        return {"ok": True, "quedan_pendientes": 0,
                "anotados_sin_escribir": len(restan),
                "aviso": ("No queda nadie por corregir en la cola. "
                          + (f"Tenés {len(restan)} anotados: confirmá con `confirmar_cola`."
                             if restan else "La cola está vacía."))}
    entrega = await ws_api.leer_entrega(
        _cli(), fila["assign_id"], fila["email"],
        str(Path(almacen.SALIDAS_DIR) / "entregas" / str(fila["assign_id"])), max_chars)
    faltan = await almacen.cola_listar(fila["assign_id"], fila["group_id"],
                                       estados=("pendiente",))
    return {"ok": True, "alumno": fila["alumno"], "email": fila["email"],
            "assign_id": fila["assign_id"], "group_id": fila["group_id"],
            "tarea": fila["tarea"], "quedan_pendientes": len(faltan), "entrega": entrega,
            "siguiente_paso": "Corregila y guardá con `anotar_correccion`. No se escribe "
                              "nada en Moodle todavía."}


@mcp.tool()
async def anotar_correccion(assign_id: str, group_id: int, email: str, nota: str,
                            mensaje: str, etiquetas: list[str] | None = None) -> dict:
    """Guarda la nota y la devolución de UN alumno en la cola. NO escribe en Moodle.

    Es el paso intermedio del lote: se acumula y recién `confirmar_cola` lo manda todo.
    `etiquetas`: los temas marcados, en kebab-case y reutilizables entre alumnos — son las
    que después alimentan `errores_frecuentes`."""
    await almacen.init_db()
    ok = await almacen.cola_anotar(assign_id, group_id, email, nota, mensaje, etiquetas or [])
    if not ok:
        # Se aceptan pendiente/anotado/error/salteado; si llegó acá, o no está en la cola o
        # ya se escribió (esas no se reabren: para cambiar una nota cargada va cargar_nota).
        return {"error": f"No pude anotar a {email} en la cola de esta tarea/comisión.",
                "posibles_motivos": [
                    "no está en la cola (¿corriste `preparar_correccion`?)",
                    "su nota YA se escribió en Moodle — para cambiarla usá `cargar_nota`",
                ]}
    faltan = await almacen.cola_listar(assign_id, group_id, estados=("pendiente",))
    listos = await almacen.cola_listar(assign_id, group_id, estados=("anotado",))
    return {"ok": True, "anotado": email, "nota": nota,
            "quedan_pendientes": len(faltan), "anotados": len(listos),
            "siguiente_paso": ("Seguí con `siguiente_para_corregir`." if faltan
                               else "No queda nadie: revisá todo con `confirmar_cola`.")}


@mcp.tool()
async def saltear_en_cola(assign_id: str, group_id: int, email: str,
                          motivo: str) -> dict:
    """Saca a un alumno de la cola SIN calificarlo, dejando registrado por qué.

    No todo lo que está pendiente se puede corregir. El caso que motivó esto: un alumno
    subió los apuntes de la cátedra en vez de su TP — no merece Aprobado ni Desaprobado,
    necesita que le avisen que suba el archivo correcto. Sin esta salida, la cola devolvía
    siempre a la misma persona y la única forma de avanzar era ponerle una nota falsa.

    Usalo también con entregas ilegibles, archivos corruptos o cualquier caso donde haga
    falta hablar con el alumno antes de poner nota. El `motivo` queda guardado y aparece
    en el resumen de `confirmar_cola`."""
    await almacen.init_db()
    ok = await almacen.cola_saltear(assign_id, group_id, email, motivo)
    if not ok:
        return {"error": f"{email} no está pendiente en la cola de esta tarea/comisión."}
    faltan = await almacen.cola_listar(assign_id, group_id, estados=("pendiente",))
    return {"ok": True, "salteado": email, "motivo": motivo,
            "quedan_pendientes": len(faltan),
            "recordatorio": "Salteado NO es calificado: este alumno sigue sin nota en "
                            "Moodle. Si hay que avisarle, usá `responder_mensaje`."}


@mcp.tool()
async def confirmar_cola(assign_id: str | None = None, group_id: int | None = None,
                         confirmado: bool = False) -> dict:
    """Escribe en Moodle TODAS las correcciones anotadas en la cola, de una.

    Con `confirmado=false` (default) devuelve el detalle completo de lo que se va a
    escribir —alumno por alumno, con su nota y su devolución— para que el OK sea informado
    y no a ciegas. Recién con `confirmado=true` se escribe.

    Cada nota se escribe y se VERIFICA por separado: si una falla, las demás siguen y el
    reporte dice exactamente cuál y por qué. Las que fallan quedan en la cola para
    reintentar; las que salen bien se registran en la bitácora."""
    await almacen.init_db()
    anotados = await almacen.cola_listar(assign_id, group_id, estados=("anotado",))
    if not anotados:
        pend = await almacen.cola_listar(assign_id, group_id, estados=("pendiente",))
        return {"ok": True, "a_escribir": 0,
                "aviso": (f"No hay nada anotado para escribir. Quedan {len(pend)} sin "
                          "corregir en la cola." if pend else "La cola está vacía.")}

    if not confirmado:
        return {
            "requiere_confirmacion": True,
            "a_escribir": len(anotados),
            "previews": [{"alumno": f["alumno"], "email": f["email"], "nota": f["nota"],
                          "etiquetas": f["etiquetas"], "devolucion": f["devolucion"]}
                         for f in anotados],
            "aviso": (f"Se van a escribir {len(anotados)} notas en Moodle. Revisalas y "
                      "volvé a llamar con confirmado=true."),
        }

    escritas, fallidas = [], []
    for f in anotados:
        res = await ws_api.cargar_nota(_cli(), f["assign_id"], f["email"], f["nota"],
                                       f["devolucion"], True)
        if res.get("ok"):
            await _registrar_bitacora(res, f["assign_id"], f["email"], f["devolucion"],
                                      f["etiquetas"], f.get("comision"))
            await almacen.cola_marcar(f["id"], "escrito")
            escritas.append({"alumno": f["alumno"], "nota": res.get("nota"),
                             "verificado": res.get("verificado")})
        else:
            motivo = res.get("error") or "no se pudo escribir"
            await almacen.cola_marcar(f["id"], "error", motivo)
            fallidas.append({"alumno": f["alumno"], "email": f["email"], "motivo": motivo})

    salida = {"ok": not fallidas, "escritas": len(escritas), "fallidas": len(fallidas),
              "detalle_escritas": escritas}
    if fallidas:
        salida["detalle_fallidas"] = fallidas
        salida["aviso"] = (f"{len(escritas)} se escribieron bien y {len(fallidas)} fallaron. "
                           "Las fallidas quedaron en la cola: arreglá el motivo y volvé a "
                           "confirmar, no se van a duplicar las que ya salieron.")
    else:
        salida["resumen"] = (f"Listo: {len(escritas)} notas escritas y verificadas. "
                             "Mirá `errores_frecuentes` para ver qué falló toda la comisión.")

    # Los salteados NO se escribieron y siguen sin nota: hay que recordarlo o se pierden.
    salteados = await almacen.cola_listar(assign_id, group_id, estados=("salteado",))
    if salteados:
        salida["salteados"] = [{"alumno": s["alumno"], "email": s["email"],
                                "motivo": s.get("resultado")} for s in salteados]
        salida["aviso_salteados"] = (
            f"⚠️ {len(salteados)} alumno(s) quedaron SALTEADOS: no se les escribió nota y "
            "siguen pendientes en Moodle. Revisá el motivo de cada uno y avisales.")
    return salida


@mcp.tool()
async def errores_frecuentes(course_id: int | None = None, assign_id: str | None = None,
                             comision: str | None = None) -> dict:
    """En qué se está equivocando TU comisión, agregado sobre las correcciones ya hechas.

    Deja de ser "qué le pasó a este alumno" y pasa a ser "qué no quedó bien explicado":
    cuando el mismo error aparece en más del 40% de los corregidos se marca `sistemico`,
    porque a esa altura el problema ya no es de los alumnos — es del material o de cómo se
    dio el tema.

    Se alimenta de las `etiquetas` que se pasan al `cargar_nota`. Si no se etiqueta al
    corregir, acá no hay nada que mostrar: **el dato no se puede reconstruir después**,
    porque exigiría releer todas las entregas de nuevo.

    Sin filtros toma toda la bitácora; se puede acotar por curso, tarea o comisión."""
    await almacen.init_db()
    r = await almacen.errores_frecuentes(course_id=course_id, assign_id=assign_id,
                                         comision=comision)
    n = r["correcciones_registradas"]
    if not n:
        return {**r, "aviso": (
            "Todavía no hay correcciones registradas con etiquetas. Se van cargando solas a "
            "medida que corregís con `cargar_nota(..., etiquetas=[...])`. Arranca vacío a "
            "propósito: es un histórico, no una foto.")}
    sistemicos = [t for t in r["temas"] if t["sistemico"]]
    salida = {**r, "temas_sistemicos": len(sistemicos)}
    if not r.get("muestra_suficiente"):
        # Con pocas correcciones el porcentaje engaña: 1 de 2 da 50%. Se muestran los temas
        # igual (sirven para ir viendo), pero sin sacar conclusiones sobre la comisión.
        top = ", ".join(f"{t['tema']} ({t['alumnos_afectados']})" for t in r["temas"][:5])
        salida["resumen"] = (
            f"Todavía son pocas correcciones ({n}, hacen falta {r['muestra_minima']}) como "
            "para hablar de la comisión: un porcentaje sobre esta cantidad engaña. "
            + (f"Por ahora aparecieron: {top}." if top else "Sin temas registrados aún.")
            + " Seguí corrigiendo y el dato se vuelve confiable solo.")
    elif sistemicos:
        cuales = ", ".join(f"{t['tema']} ({t['porcentaje']}%)" for t in sistemicos[:5])
        salida["resumen"] = (
            f"Sobre {n} corrección/es: {cuales}. Con esa proporción no es un problema "
            "individual — conviene reforzar el tema con toda la comisión.")
    else:
        salida["resumen"] = (f"Sobre {n} corrección/es no hay ningún error que se repita en "
                             "más del 40%: los desvíos vienen siendo individuales.")
    return salida


if __name__ == "__main__":
    # Transport stdio: lo lanza el propio Claude Code del tutor como MCP local.
    mcp.run()
