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
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from moodle import active_ia, almacen, auditoria, informes, snapshot, ws_api
from moodle.cliente import MobileWSClient

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
    if not datos:
        return {
            "vacio": True,
            "aviso": "Sin datos guardados. Corré descubrir_cursos / descubrir_comisiones / "
                     "listar_tareas, confirmá el mapeo con el tutor y guardalo con guardar_mis_datos.",
        }
    return {"actualizado_at": await almacen.mis_datos_actualizada(), "datos": datos}


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
            "mensaje": f"El refresco tardó más de {_REFRESCO_TIMEOUT_S}s y se cortó. "
                       "Puede que tengas muchas comisiones/tareas o que Moodle esté lento; "
                       "reintentá en un rato.",
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
    (API REST: mod_assign_list_participants.)"""
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
async def buscar_alumno(texto: str) -> list[dict]:
    """Busca un alumno por NOMBRE (o email) en el caché del snapshot y devuelve su
    situación: comisión, último acceso, entregas por tarea (estado y nota) y pendientes.
    Es la forma rápida de responder 'qué debe X' / 'traza de X' sin pedir el email. Corré
    actualizar_tableros antes si el caché está vacío. Devuelve varias coincidencias si el
    nombre es ambiguo."""
    await almacen.init_db()
    return await almacen.buscar_alumnos(texto)


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
    apps Google (NotebookLM/Colab) en abren / piden cuenta. Requiere Playwright instalado
    (`pip install playwright && playwright install chromium`); si no está, se saltea con
    aviso y la auditoría por API igual corre. Tarda más (abre una página por cuestionario).

    Es READ-ONLY sobre Moodle: no escribe nada en el campus, solo el worksheet local.
    `course_id` sale de `aulas` / `descubrir_cursos`. `materia` es el nombre para el
    encabezado (ej. 'Programación 2'); `evaluador`/`rol` son opcionales (dejá `evaluador`
    vacío para firmar como Celda de Control de Calidad)."""
    return await auditoria.auditar_aula(
        _cli(), course_id, almacen.SALIDAS_DIR, materia=materia, evaluador=evaluador,
        rol=rol, con_navegador=con_navegador, unidad=unidad)


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
    corregir. Trae contadores de en espera / corregidos / sin entrega. (API REST de
    Active-IA, JWT — no toca Moodle.)"""
    return await active_ia.activeia_pendientes()


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

    Es una ESCRITURA (la corrección carga la nota/devolución del alumno): llamá primero
    con confirmado=false para previsualizar qué se va a corregir; recién tras el OK del
    tutor, confirmado=true. Antes conseguí comision_id/rubrica_id con
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
            "aviso": "Esto baja la entrega, la corrige con Active-IA (Gemini) y carga la "
                     "nota/devolución del alumno. Revisalo y volvé a llamar con "
                     "confirmado=true para ejecutar.",
        }
    return await active_ia.corregir_con_active_ia(
        _cli(), assign_id, email, comision_id, rubrica_id,
        alumno_nombre=alumno_nombre, moodle_url=moodle_url, timeout_s=timeout_s,
    )


# ---------- ESCRITURA (con confirmación) ----------
@mcp.tool()
async def cargar_nota(assign_id: str, email: str, nota: str, mensaje: str,
                      confirmado: bool = False) -> dict:
    """Escribe nota + devolución en Moodle. Llamá primero con confirmado=false para
    previsualizar; recién tras el OK del tutor, confirmado=true.

    La nota depende del TIPO de calificación de la tarea:
    - **TPs (ESCALA)**: pasá el texto EXACTO 'Aprobado' o 'Desaprobado' (NO un número).
      Si no coincide, devuelve es_escala=true + la lista de opciones válidas.
    - **Integrador (TIO) / numéricas**: pasá el número (coma decimal, ej. '9,85').
    Devuelve `verificado` (relee la nota por API para confirmar el guardado). (API REST:
    mod_assign_save_grade — sin navegador.)"""
    return await ws_api.cargar_nota(_cli(), assign_id, email, nota, mensaje, confirmado)


if __name__ == "__main__":
    # Transport stdio: lo lanza el propio Claude Code del tutor como MCP local.
    mcp.run()
