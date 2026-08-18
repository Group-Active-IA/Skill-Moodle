"""Operaciones de alto nivel sobre la API REST de Moodle (token mobile).

Reemplaza el scraping HTML por web services oficiales. Cada función recibe un cliente
con `.ws(fn, params)` (HybridMoodleClient) y devuelve datos limpios; ninguna lanza:
el error se devuelve como dict con "error" (mismo contrato que actions.py/foros.py).

Gotcha central: los WS de assign usan el **instanceid** del assign, mientras que el
copiloto identifica las tareas por **cmid** (el id de mod/assign/view.php?id=). Por eso
`_assign_map` construye y cachea el mapa cmid -> {id, course, grade} una vez por cliente."""

import html as _html
import logging
import re

from .cliente import MoodleWSError

log = logging.getLogger("skill.ws_api")

_TAGS_RE = re.compile(r"<[^>]+>")

# Escalas conocidas del campus TUP: scaleid -> {etiqueta_normalizada: valor_a_guardar}.
# TODAS se confirmaron leyendo el <select name="grade"> del grader real. NO son
# guessables: la 5 va invertida (1=Aprobado, 2=Desaprobado) y la 3 va en orden creciente.
# Para relevar una escala nueva:
#   /mod/assign/view.php?id=<cmid>&rownum=0&action=grade  →  mirar el <select>
_ESCALAS: dict[int, dict[str, int]] = {
    # Actividades de cierre de Prog I, II, III y algunas de Prog IV.
    5: {"aprobado": 1, "desaprobado": 2},
    # Verificada el 2026-08-04 en el cmid 20763 → 16211 de Prog IV (unidad 8).
    3: {"no satisfactorio": 1, "satisfactorio": 2, "supera lo esperado": 3},
}


def _plano(texto: str, limite: int = 1500) -> str:
    t = _html.unescape(_TAGS_RE.sub(" ", texto or ""))
    return re.sub(r"\s+", " ", t).strip()[:limite]


def _norm(texto: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


# ---------- Mapa cmid -> instanceid (cacheado por cliente) ----------

# El mapa se cachea por proceso, pero VENCE. El server MCP vive horas y las aulas se
# editan mientras corre: al arrancar un cuatrimestre la cátedra cambia el tipo de
# calificación de una tarea y el caché eterno seguía sirviendo la foto vieja. Eso ya rompió
# de verdad (2026-08-04, cmid 20763: el WS decía escala y el caché decía numérica, con lo
# cual `cargar_nota` pedía un número para un <select> de Aprobado/Desaprobado).
_ASSIGN_MAP_TTL_S = 300.0


async def _assign_map(client, refrescar: bool = False) -> dict:
    """{cmid(str): {"id": instanceid, "course": courseid, "grade": gradeval, "name": str}}.
    `grade` < 0 → escala (-scaleid); ≥ 0 → nota numérica (puntos máx).

    El caché vence a los `_ASSIGN_MAP_TTL_S` segundos. `refrescar=True` lo ignora: lo usan
    las operaciones donde una foto vieja se escribe en el legajo de un alumno."""
    import time as _time

    cache = getattr(client, "_assign_map_cache", None)
    sellado = getattr(client, "_assign_map_cache_ts", 0.0)
    if cache is not None and not refrescar and (_time.monotonic() - sellado) < _ASSIGN_MAP_TTL_S:
        return cache
    data = await client.ws("mod_assign_get_assignments")
    m: dict = {}
    for c in (data or {}).get("courses", []):
        for a in c.get("assignments", []):
            # `feedback_file` dice si la tarea acepta ARCHIVOS de retroalimentación. En el
            # campus TUP está apagado en todas (sólo `comments` y `editpdf`): mandar un
            # archivo igual hace que Moodle lo ignore en silencio y la tool reporte un
            # éxito que no ocurrió.
            # TRI-ESTADO a propósito: None = el WS no trajo `configs`, o sea que NO SÉ.
            # Con un booleano, "no pude leer la config" se volvía "está deshabilitado", y
            # el aviso al tutor afirmaba algo que nadie verificó. Misma doctrina que
            # `es_escala` y que `disponible` en version_skill.
            # `cfg` y no `c`: `c` es el curso del bucle de afuera. La genexp tiene scope
            # propio y hoy no rompe, pero desarmarla en un `for` la rompería en silencio.
            configs = a.get("configs") or []
            acepta_archivos = any(
                cfg.get("subtype") == "assignfeedback" and cfg.get("plugin") == "file"
                and cfg.get("name") == "enabled" and str(cfg.get("value")) == "1"
                for cfg in configs
            ) if configs else None
            m[str(a.get("cmid"))] = {
                "id": a.get("id"),
                "course": a.get("course"),
                "grade": a.get("grade"),
                "name": a.get("name", ""),
                "feedback_file": acepta_archivos,
                # FECHA DE ENTREGA. Venía en esta misma respuesta y se estaba tirando, y esa
                # ausencia es la que rompía `alumnos_en_riesgo`: sin `duedate` no se puede
                # distinguir "no entregó" de "todavía no vencía", así que contaba como
                # abandono cualquier actividad futura y marcaba al padrón entero en rojo
                # (94 de 94 en Prog I, donde ninguna actividad de cierre tiene fecha).
                # 0 = la actividad NO tiene fecha de entrega. No es "vence en 1970".
                "duedate": int(a.get("duedate") or 0),
            }
    # Sólo pisamos el caché si el WS devolvió algo. Una respuesta vacía (caída, permisos)
    # no debe borrar un mapa bueno ni, peor, dejar el diccionario vacío como verdad.
    if m:
        client._assign_map_cache = m
        client._assign_map_cache_ts = _time.monotonic()
        return m
    return cache or {}


async def _instanceid(client, cmid: str) -> int | None:
    return (await _assign_map(client)).get(str(cmid), {}).get("id")


async def listar_tareas(client, course_id: int) -> list[dict]:
    """Tareas del curso (TPs, parciales, integrador) con su cmid (=assign_id que usan
    las tools) y título, por API REST (mod_assign_get_assignments, ya cacheado en
    `_assign_map`). Reemplaza el scraping de mod/assign/index.php de actions.py."""
    amap = await _assign_map(client)
    tareas = [
        {"id": cmid, "titulo": cfg.get("name", ""), "instanceid": cfg.get("id")}
        for cmid, cfg in amap.items()
        if cfg.get("course") == course_id
    ]
    tareas.sort(key=lambda t: t["titulo"].lower())
    return tareas


# ---------- email -> userid ----------

async def _userid_por_email(client, email: str) -> tuple[int | None, str | None]:
    try:
        res = await client.ws(
            "core_user_get_users_by_field", {"field": "email", "values": [email]}
        )
        if isinstance(res, list) and res:
            return int(res[0]["id"]), res[0].get("fullname")
    except MoodleWSError:
        pass
    return None, None


# ---------- LECTURA de calificación (list_participants) ----------

async def _participantes(client, cmid: str, group_id: int) -> list[dict] | dict:
    inst = await _instanceid(client, cmid)
    if inst is None:
        return {"error": f"No encontré la tarea con cmid={cmid} (¿existe / es de tu curso?)."}
    try:
        return await client.ws(
            "mod_assign_list_participants",
            {"assignid": inst, "groupid": group_id, "filter": "", "skip": 0, "limit": 0,
             "onlyids": 0, "includeenrolments": 0},
        )
    except MoodleWSError as e:
        return {"error": f"list_participants falló: {e.errorcode}"}


def _es_estudiante(p: dict) -> bool:
    """El filtro compartido de "esta persona cuenta en los informes".

    **Una matrícula SUSPENDIDA es una baja del aula, no un alumno con problemas.** El
    suspendido sigue figurando en la lista de participantes, así que sin este filtro aparece
    como deudor, como pendiente, como retrasado y como desenganchado — infla los cuatro
    informes con gente que ya no cursa. Y lo hace en silencio: los números quedan plausibles.

    El flag `suspended` lo trae **sólo** `mod_assign_list_participants`. Los usuarios de
    `core_enrol_get_enrolled_users` no lo traen, pero esos ya vienen filtrados con
    `onlyactive: 1` en origen, así que acá `.get` devuelve `None` y no los toca. Por eso este
    es EL interruptor de "solo activos" para todo lo que sale de las vistas de tarea:
    `sumario`, `pendientes_por_corregir`, `entregas_tarea`, el snapshot y el panorama.

    Aporte de un tutor del equipo (2026-08-17), verificado antes de aplicarlo: el campo existe
    en la respuesta real y hoy da 0 suspendidos en las comisiones medidas, así que no cambia
    ningún número — es una garantía para cuando alguien se dé de baja a mitad de cuatrimestre.
    """
    if p.get("suspended"):
        return False
    roles = p.get("roles") or []
    return any(r.get("shortname") == "student" for r in roles) or not roles


async def sumario(client, cmid: str, group_id: int = 0) -> dict:
    """Conteo OFICIAL por API: participantes / enviados / pendientes por calificar.
    Reemplaza el scraping del 'Sumario de calificaciones'. Cuenta solo estudiantes."""
    ps = await _participantes(client, cmid, group_id)
    if isinstance(ps, dict):
        return ps
    est = [p for p in ps if _es_estudiante(p)]
    enviados = sum(1 for p in est if p.get("submitted"))
    pendientes = sum(1 for p in est if p.get("submitted") and p.get("requiregrading"))
    return {"assign_id": cmid, "group_id": group_id,
            "participantes": len(est), "enviados": enviados, "pendientes": pendientes}


def _grupo_visible(p: dict, group_id: int = 0) -> str | None:
    """Nombre del grupo a mostrar para un participante.

    Un alumno pertenece a varios grupos a la vez (su comisión + grupos auxiliares tipo
    `Grupo_251`). Agarrar el primero de la lista mostraba el auxiliar, así que el tutor
    veía "Grupo_389" en vez de "M26 C1-25". Si se filtró por comisión, esa es la que hay
    que mostrar."""
    gs = p.get("groups") or []
    if group_id:
        for g in gs:
            if g.get("id") == group_id:
                return g.get("name")
    # Sin filtro: preferimos algo que parezca comisión antes que un grupo auxiliar.
    nombres = [g.get("name") for g in gs if g.get("name")]
    real = [n for n in nombres if not _norm(n).startswith("grupo_")]
    return (real or nombres or [None])[0]


async def pendientes_tarea(client, cmid: str, group_id: int = 0) -> dict:
    """Alumnos que entregaron y siguen SIN NOTA efectiva.

    Son DOS casos, no uno, y el segundo era invisible:
      - `requiere_correccion`: Moodle los marca `requiregrading`. La cola normal.
      - `calificado_sin_nota`: se guardó la devolución pero la nota quedó en "sin
        calificar". El flag `requiregrading` queda APAGADO, así que desaparecen de la cola
        **sin tener nota**: ni pendientes ni calificados. Mirando sólo `requiregrading`
        no aparecen — tres alumnos estuvieron así tres semanas sin que ninguna tool los
        mostrara, y sin que nadie los estuviera esperando.
    """
    amap = await _assign_map(client)
    cfg = amap.get(str(cmid))
    ps = await _participantes(client, cmid, group_id)
    if isinstance(ps, dict):
        return ps
    gmap = await grades_map(client, cfg["id"]) if cfg and cfg.get("id") else {}

    alumnos = []
    for p in ps:
        if not (_es_estudiante(p) and p.get("submitted")):
            continue
        requiere = bool(p.get("requiregrading"))
        sin_nota = nota_display(cfg.get("grade") if cfg else 0, gmap.get(p.get("id"))) is None
        if not requiere and not sin_nota:
            continue
        alumnos.append({
            "name": p.get("fullname"),
            "email": p.get("email"),
            "userid": p.get("id"),
            "grupo": _grupo_visible(p, group_id),
            "motivo": "requiere_correccion" if requiere else "calificado_sin_nota",
        })

    sin_nota_n = sum(1 for a in alumnos if a["motivo"] == "calificado_sin_nota")
    out = {
        "assign_id": cmid, "group_id": group_id,
        "pendientes": len(alumnos),
        "requieren_correccion": len(alumnos) - sin_nota_n,
        "calificados_sin_nota": sin_nota_n,
        "alumnos": alumnos,
    }
    if sin_nota_n:
        out["aviso"] = (
            f"{sin_nota_n} entrega(s) figuran como corregidas pero NO tienen nota: la "
            "devolución se guardó y la calificación quedó vacía. No aparecen en ninguna "
            "cola, así que hay que cargarles la nota a mano."
        )
    return out


async def grades_map(client, inst: int) -> dict:
    """{userid: valor_de_nota} de una tarea (la última por usuario). {} ante error.

    Vive acá (y no en `snapshot`) porque la usan los dos: el snapshot para persistir y
    `entregas_tarea` para responder en vivo. `snapshot` importa `ws_api`, nunca al revés."""
    try:
        g = await client.ws("mod_assign_get_grades", {"assignmentids": [inst]})
    except Exception as e:  # noqa: BLE001
        log.warning("get_grades(%s) falló: %s", inst, type(e).__name__)
        return {}
    m: dict = {}
    for a in (g or {}).get("assignments", []):
        for gr in a.get("grades", []):
            u = gr.get("userid")
            if u is None:
                continue
            tm = gr.get("timemodified", 0) or 0
            if u not in m or tm > m[u][1]:
                m[u] = (gr.get("grade"), tm)
    return {u: v[0] for u, v in m.items()}


async def entregas_tarea(client, cmid: str, group_id: int = 0) -> dict:
    """Padrón COMPLETO de una tarea: quién entregó, quién no, y con qué nota.

    Es el atajo en vivo del snapshot: dos requests (list_participants + get_grades) para
    UNA tarea, en vez de recorrer todas las comisiones × todas las tareas del tutor.
    No escribe caché: lee del campus y responde.

    La distinción que importa (y que `pendientes_por_corregir` no da): "0 pendientes de
    corregir" NO es "todos al día" — el que no entregó nada tiene 0 para corregir y es el
    que más debe. Por eso acá `sin_entrega` viene separado y con nombre y apellido.
    """
    amap = await _assign_map(client)
    cfg = amap.get(str(cmid))
    if not cfg or not cfg.get("id"):
        return {"error": f"No encontré la tarea con cmid={cmid} (¿existe / es de tu curso?)."}

    ps = await _participantes(client, cmid, group_id)
    if isinstance(ps, dict):
        return ps

    gmap = await grades_map(client, cfg["id"])
    est = [p for p in ps if _es_estudiante(p)]

    alumnos = []
    for p in est:
        entregado = bool(p.get("submitted"))
        requiere = bool(p.get("requiregrading"))
        nota = nota_display(cfg.get("grade"), gmap.get(p.get("id")))
        if not entregado:
            estado = "Sin entrega"
        elif requiere:
            estado = "Enviado para calificar"
        elif nota is None:
            # El limbo: Moodle lo da por corregido (requiregrading apagado) pero la nota
            # quedó vacía. Sin este estado se contaba como "Calificado" y el alumno
            # desaparecía sin nota, sin figurar en ninguna cola.
            estado = "Calificado SIN nota"
        else:
            estado = "Calificado"
        alumnos.append({
            "nombre": p.get("fullname"),
            "email": (p.get("email") or "").lower(),
            "userid": p.get("id"),
            "estado": estado,
            "nota": nota,
            "entregado": entregado,
            "pendiente": entregado and (requiere or nota is None),
        })

    # Los que deben primero, después lo que falta hacer, y al final los cerrados.
    _orden = {"Sin entrega": 0, "Calificado SIN nota": 1, "Enviado para calificar": 2,
              "Calificado": 3}
    alumnos.sort(key=lambda a: (_orden[a["estado"]], _norm(a["nombre"] or "")))

    sin_nota = sum(1 for a in alumnos if a["estado"] == "Calificado SIN nota")
    out = {
        "assign_id": str(cmid),
        "group_id": group_id,
        "tarea": cfg.get("name", ""),
        "participantes": len(est),
        "entregados": sum(1 for a in alumnos if a["entregado"]),
        "pendientes_por_corregir": sum(1 for a in alumnos if a["estado"] == "Enviado para calificar"),
        "calificados_sin_nota": sin_nota,
        "sin_entrega": sum(1 for a in alumnos if not a["entregado"]),
        "alumnos": alumnos,
    }
    if sin_nota:
        out["aviso"] = (
            f"⚠️ {sin_nota} entrega(s) figuran como corregidas pero NO tienen nota. "
            "No salen en ninguna cola de pendientes: hay que cargarles la nota a mano."
        )
    return out


# ---------- BÚSQUEDA de alumno EN VIVO ----------

_ROL_ALUMNO = "student"


def _dias_desde(ts) -> int | None:
    """Días enteros desde un epoch de Moodle. `None` si nunca entró (lastaccess=0)."""
    import time

    try:
        t = int(ts or 0)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return max(0, int((time.time() - t) // 86400))


async def _alumnos_de_comision(client, course_id: int, group_id: int) -> list[dict] | dict:
    """Matriculados de UNA comisión, ya filtrados a rol alumno.

    `core_enrol_get_enrolled_users` devuelve también al docente (el tutor figura como
    `editingteacher` en su propia comisión): sin filtrar por rol, buscar el nombre del
    propio tutor lo devolvería a él mismo como si fuera un alumno."""
    try:
        data = await client.ws(
            "core_enrol_get_enrolled_users",
            {
                "courseid": course_id,
                "options": [
                    {"name": "groupid", "value": group_id},
                    {"name": "onlyactive", "value": 1},
                ],
            },
        )
    except MoodleWSError as e:
        return {"error": f"No pude leer los matriculados del grupo {group_id}: {e}"}
    return [
        u
        for u in (data or [])
        if any(r.get("shortname") == _ROL_ALUMNO for r in u.get("roles", []))
    ]


async def buscar_alumnos(client, texto: str, cursos: list[dict], limite: int = 8) -> dict:
    """Busca un alumno EN VIVO por nombre o email en las comisiones del tutor.

    Antes esto leía el caché del snapshot, y eso lo hacía mentir: si el snapshot no había
    corrido, devolvía lista vacía — indistinguible de "ese alumno no existe". Un tutor
    recién instalado siempre veía []. Ahora se consulta el campus: una request por
    comisión (~1 s cada una, en paralelo), sin ningún setup previo.

    `cursos` es la lista de "Mis datos" (course_id + comisiones_del_tutor). El match es
    por substring sobre nombre o email, insensible a mayúsculas y acentos."""
    import asyncio

    q = _norm(texto)
    if not q:
        return {"error": "Decime un nombre o email para buscar."}

    objetivos = [
        (c.get("course_id"), c.get("nombre", ""), com.get("comision", ""), com.get("group_id"))
        for c in cursos
        for com in c.get("comisiones_del_tutor", [])
        if c.get("course_id") and com.get("group_id")
    ]
    if not objetivos:
        return {
            "error": "No tenés comisiones mapeadas en 'Mis datos', así que no sé dónde buscar.",
            "siguiente_paso": "Corré mi_comision(tu nombre) y guardá con guardar_mis_datos.",
        }

    sem = asyncio.Semaphore(5)  # el campus es compartido: no lo martillamos

    async def _una(course_id, curso_nombre, comision, group_id):
        async with sem:
            return (course_id, curso_nombre, comision, group_id,
                    await _alumnos_de_comision(client, course_id, group_id))

    resultados = await asyncio.gather(
        *(_una(*o) for o in objetivos), return_exceptions=True
    )

    coincidencias: dict[int, dict] = {}
    avisos: list[str] = []
    revisadas = 0
    for res in resultados:
        if isinstance(res, BaseException):
            avisos.append(f"Una comisión falló: {type(res).__name__}: {res}")
            continue
        course_id, curso_nombre, comision, group_id, alumnos = res
        if isinstance(alumnos, dict):  # error de esa comisión: se avisa, no se traga
            avisos.append(f"{comision}: {alumnos.get('error')}")
            continue
        revisadas += 1
        for u in alumnos:
            nombre, email = u.get("fullname", ""), (u.get("email") or "").lower()
            if q not in _norm(nombre) and q not in email:
                continue
            uid = u.get("id")
            if uid in coincidencias:  # el mismo alumno en dos comisiones del tutor
                coincidencias[uid]["comisiones"].append(comision)
                continue
            estado_aula, dias_aula = _lectura_aula(u)
            coincidencias[uid] = {
                "nombre": nombre,
                "email": email,
                "userid": uid,
                "curso": curso_nombre,
                "course_id": course_id,
                "comisiones": [comision],
                "group_id": group_id,
                # Los DOS relojes con nombre propio. Antes acá había un `dias_sin_entrar` que
                # era del SITIO y se leía como de la materia: un alumno que entra todos los
                # días para otra cosa y nunca abrió ésta figuraba con 0, o sea al día.
                "estado_aula": estado_aula,
                "dias_sin_abrir_la_materia": dias_aula,
                "dias_sin_entrar_al_campus": _dias_desde(u.get("lastaccess")),
            }

    hallados = sorted(coincidencias.values(), key=lambda a: _norm(a["nombre"]))
    salida = {
        "ok": True,
        "buscado": texto,
        "encontrados": len(hallados),
        "coincidencias": hallados[:limite],
        "_meta": {
            "fuente": "vivo",
            "comisiones_revisadas": revisadas,
            "comisiones_pedidas": len(objetivos),
            "degradado": bool(avisos),
            "avisos": avisos,
        },
    }
    if not hallados:
        # Sin coincidencias NO es lo mismo que sin datos: se dice cuál de las dos es.
        salida["detalle"] = (
            f"Revisé {revisadas} comisión/es en vivo y no hay nadie que matchee '{texto}'."
            if revisadas
            else "No pude leer ninguna comisión, así que no sé si existe o no."
        )
    if len(hallados) > limite:
        salida["_meta"]["avisos"].append(
            f"Hay {len(hallados)} coincidencias, muestro las primeras {limite}."
        )
    return salida


# ---------- LOS DOS RELOJES DE ACCESO (vocabulario compartido) ----------

# `core_enrol_get_enrolled_users` devuelve DOS relojes por alumno y no son el mismo:
# `lastaccess` es la última vez que pisó el CAMPUS y `lastcourseaccess` la última vez que
# abrió ESTA materia. La skill leía sólo el primero y lo llamaba `dias_sin_entrar`, así que
# mentía justo en el caso que más importa: el que entra todos los días para otra materia y
# nunca abre la propia figuraba con `0` — perfecto para la herramienta, desaparecido en la
# realidad. Al revés falla igual: el "Nunca" de la página de participantes es el reloj del
# curso, y ahí el que abandonó y el que cursa activo sin abrir esta materia se ven idénticos.
#
# **Lo que hacía el bug no era devolver un número impreciso: era borrar gente de la lista.**
# `alumnos_en_riesgo` no devuelve los verdes, y con el reloj del sitio en 0 el clasificador
# daba verde. Corrido contra el código de HEAD con los datos que relevó un tutor de Prog III
# y Prog IV: de 6 alumnos que había que contactar, **5 daban verde** —o sea, invisibles— y el
# sexto salía como "9 días sin entrar", ocultando que NUNCA había abierto la materia.
#
# Verificado en vivo el 2026-08-13 sobre los 119 alumnos de las cuatro comisiones de Juani
# (Prog I com6/com12, Prog IV com7/com8): el campo llega en el 100% de los alumnos, 10 entran
# al campus sin haber abierto NUNCA la materia y en 22 los dos relojes difieren un día o más.
# El dato ya lo estábamos recibiendo y lo tirábamos: no hace falta navegador.
#
# Sobre permisos: los dos campos comparten el MISMO gate en Moodle (`user/lib.php`, líneas
# 505 y 514 en MOODLE_405_STABLE — a los dos los tapa `$CFG->hiddenuserfields` conteniendo
# 'lastaccess', NO una capability distinta por rol). Entonces a cualquier tutor al que hoy le
# funcione el reloj del sitio le va a llegar también el del curso, sea `editingteacher` o
# `teacher`. No se pudo probar con un token `teacher` (no tenemos sus credenciales; en Prog I
# hay 20 con ese rol), pero si algún día no llega, se trata como `sin_dato` y se grita: nunca
# se lo hace pasar por "nunca abrió".

_AULA_ABRIO, _AULA_NUNCA, _AULA_SIN_DATO = "abrio", "nunca_abrio", "sin_dato"

# Moodle actualiza los dos relojes con bandas muertas de 60 s independientes
# (`LASTACCESS_UPDATE_SECS`, `lib/datalib.php`), así que el del curso puede quedar hasta un
# minuto ADELANTADO respecto al del sitio. Se vio: 10 alumnos con desfasaje, máximo 46 s.
# Es normal, no dato roto — pero por eso la brecha se mide en días enteros y no en segundos.
_BRECHA_MINIMA_DIAS = 1

# Cuántos días sin abrir la materia empiezan a significar algo. El umbral no es decoración:
# la primera versión de `sin_entrar_al_aula` marcaba "para contactar" con sólo mirar la
# brecha entre los dos relojes, y en com6 daba 18 de 36 alumnos — la mitad del padrón. Una
# lista con media comisión adentro no la mira nadie, que es exactamente lo que ya le pasa a
# `alumnos_en_riesgo` cuando devuelve 69/69 en rojo. Que alguien abra la materia dos veces
# por semana y entre al campus todos los días NO es desenganche.
_AULA_DESENGANCHE_DIAS = 7


def _lectura_aula(u: dict) -> tuple[str, int | None]:
    """(estado, días sin abrir ESTA materia) de un alumno crudo del web service.

    Devuelve un estado y no sólo un número porque el número solo miente en los dos
    extremos: un `0` se lee como "entró hoy" y un `99999` como "hace 273 años". Son tres
    situaciones distintas y hay que poder distinguirlas:

    - `abrio`     → hay timestamp: días enteros desde que abrió la materia.
    - `nunca_abrio` → el campo vino en 0. Nunca la abrió. No es lo mismo que abandono:
      puede haberse matriculado ayer.
    - `sin_dato`  → el campo NO vino (permisos, respuesta incompleta). Acá no se sabe, y
      decir "nunca abrió" sería inventar."""
    if "lastcourseaccess" not in u:
        return _AULA_SIN_DATO, None
    try:
        ts = int(u.get("lastcourseaccess") or 0)
    except (TypeError, ValueError):
        return _AULA_SIN_DATO, None
    if ts <= 0:
        return _AULA_NUNCA, None
    return _AULA_ABRIO, _dias_desde(ts)


# ---------- ALUMNOS EN RIESGO (cruce de señales, lógica pura + consulta) ----------

_RIESGO_DIAS_ALERTA = 14
_RIESGO_DIAS_AVISO = 7

# Las actividades de cierre de unidad son la cadencia semanal de la cursada: dejar de
# entregarlas ES la señal de abandono. El Integrador (grupal, con dupla, una sola entrega
# al final) y los parciales tienen otra dinámica — medido en vivo, 13 de 16 alumnos de una
# comisión no habían entregado el Integrador, así que contarlo en la racha marcaba en
# amarillo a gente que venía entrando y entregando todo al día. Ruido que hace que después
# nadie mire el tablero.
_RE_CIERRE_UNIDAD = re.compile(r"actividad\s+de\s+cierre|unidad\s*\d+", re.I)


def es_actividad_de_cierre(titulo: str) -> bool:
    """Si una tarea es de la cadencia semanal (cuenta para la racha) o no."""
    t = _norm(titulo)
    if any(p in t for p in ("integrador", "parcial", "recuperatorio", "tio", "extraordinari")):
        return False
    return bool(_RE_CIERRE_UNIDAD.search(t))


def _vencidas(orden: list[str], duedates: dict, ahora: int) -> tuple[list[str], list[str]]:
    """Parte las tareas en (vencidas, no_exigibles). PURA.

    **Sólo lo VENCIDO puede contarse como no entregado.** Sin `duedate` no se puede distinguir
    "no entregó" de "todavía no vencía", y tratar las dos igual es lo que hacía que
    `alumnos_en_riesgo` marcara 94 de 94 alumnos en rojo en Prog I — un curso donde NINGUNA de
    las 10 actividades de cierre tiene fecha. Una alarma que marca a todos no es una alarma, y
    encima envenena la confianza en el resto de la herramienta.

    `duedate == 0` es "no tiene fecha", no "venció en 1970": va a `no_exigibles` junto con las
    que vencen más adelante. Que el conteo baje mucho es el punto, no un efecto secundario.
    """
    vencidas, fuera = [], []
    for aid in orden:
        d = int((duedates.get(str(aid)) or 0))
        (vencidas if 0 < d <= ahora else fuera).append(str(aid))
    return vencidas, fuera


def _racha_final_sin_entregar(estados: list[str]) -> int:
    """Cuántas tareas seguidas quedaron sin entregar CONTANDO DESDE LA ÚLTIMA.

    Se mira la racha final y no el total a propósito: alguien que no entregó el TP1 hace
    tres meses pero viene entregando los últimos cinco NO está abandonando. El que dejó de
    entregar las últimas dos, sí. El total esconde esa diferencia; la racha la muestra.

    Recibe SÓLO los estados de las tareas ya vencidas (ver `_vencidas`): una actividad que
    todavía no venció no puede formar parte de una racha de abandono."""
    racha = 0
    for estado in reversed(estados):
        if estado == "Sin entrega":
            racha += 1
        else:
            break
    return racha


def _motivos_racha(racha: int) -> list[str]:
    if racha >= 2:
        return [f"{racha} tareas seguidas sin entregar"]
    if racha == 1:
        return ["la última tarea sin entregar"]
    return []


def _clasificar_riesgo(estado_aula: str, dias_aula: int | None, racha: int,
                       dias_campus: int | None = None,
                       dias_alerta: int = _RIESGO_DIAS_ALERTA,
                       dias_aviso: int = _RIESGO_DIAS_AVISO) -> tuple[str, list[str]]:
    """(nivel, motivos). nivel: 'rojo' | 'amarillo' | 'verde' | 'sin_datos'.

    **El reloj que manda es el de la MATERIA** (`dias_aula`), no el del campus. Antes esto
    recibía un solo número y era el del sitio, y por eso borraba gente: alguien que entra a
    Moodle todos los días para otra materia daba 0 días → verde → y los verdes no se
    devuelven. Corrido contra el código viejo con datos reales de un tutor, 5 de 6 alumnos
    que había que contactar salían verdes. El campus sigue entrando en la cuenta, pero en el
    rol que de verdad tiene: distinguir al que ELIGE no abrir la materia (aparece por el
    campus) del que no está en ninguna parte.

    `estado_aula` viene de `_lectura_aula` y decide la rama:

    - `sin_dato` → no se midió. **Nunca cae en verde**: si la racha sola no alcanza para
      marcarlo, devuelve `sin_datos`, porque un verde acá es "no pude leerlo" disfrazado de
      "está al día", el error que este proyecto ya pagó varias veces.
    - `nunca_abrio` → no abrió la materia ni una vez. Rojo si además dejó de entregar algo
      vencido o si hace mucho que tampoco pisa el campus; si no venció nada todavía queda
      amarillo **a propósito**: puede haberse matriculado esta semana y acá no se ve la
      fecha de matriculación, así que marcarlo rojo sería el padrón entero en rojo de nuevo.
    - `abrio` → los umbrales corren sobre los días sin abrir la materia."""
    motivos: list[str] = []

    if estado_aula == _AULA_SIN_DATO:
        motivos.append("no se pudo leer su último acceso a la materia (no es que no la haya "
                       "abierto: no se sabe)")
        motivos += _motivos_racha(racha)
        if racha >= 2:
            return "rojo", motivos
        return ("amarillo" if racha == 1 else "sin_datos"), motivos

    # "Aparece por el campus" es lo que separa al que eligió no entrar del que no está.
    activo_en_campus = dias_campus is not None and dias_campus <= dias_aviso

    if estado_aula == _AULA_NUNCA:
        if dias_campus is None:
            # Nunca abrió la materia y nunca entró al campus: el caso más extremo, y el
            # único donde "nunca entró al campus" es una frase verdadera.
            motivos.append("nunca abrió la materia, y nunca entró al campus tampoco")
            motivos += _motivos_racha(racha)
            return "rojo", motivos
        if activo_en_campus:
            motivos.append(f"nunca abrió la materia, pero entró al campus hace "
                           f"{dias_campus} día(s): está cursando otra cosa")
        else:
            motivos.append(f"nunca abrió la materia, y hace {dias_campus} día(s) que tampoco "
                           "entra al campus")
        motivos += _motivos_racha(racha)
        if racha >= 1 or dias_campus > dias_alerta:
            return "rojo", motivos
        return "amarillo", motivos

    if dias_aula > dias_aviso:
        motivos.append(f"{dias_aula} días sin abrir la materia")
    # La brecha entre los dos relojes: no perdió el acceso, eligió no entrar. Es una señal
    # más fuerte que los días solos —el que no entra a Moodle en diez días puede estar
    # enfermo o de viaje; el que entra todos los días y saltea TU materia, no.
    eligio_no_entrar = (activo_en_campus and dias_aula > dias_aviso
                        and dias_aula - dias_campus >= _BRECHA_MINIMA_DIAS)
    if eligio_no_entrar:
        motivos.append(f"y entró al campus hace {dias_campus} día(s): está activo en otra "
                       "materia, no perdió el acceso")
    motivos += _motivos_racha(racha)

    # Dos tareas seguidas en cero pesan más que los días: alguien puede no entrar una
    # semana por trabajo y estar al día, pero dejar de entregar es abandono empezando.
    if racha >= 2:
        return "rojo", motivos
    if eligio_no_entrar:
        return "rojo", motivos
    if dias_aula > dias_alerta and racha >= 1:
        return "rojo", motivos
    if dias_aula > dias_alerta or racha == 1 or dias_aula > dias_aviso:
        return "amarillo", motivos
    return "verde", motivos


async def alumnos_en_riesgo(client, course_id: int, group_id: int, tareas: list[dict],
                            dias_alerta: int = _RIESGO_DIAS_ALERTA,
                            dias_aviso: int = _RIESGO_DIAS_AVISO) -> dict:
    """Cruza último acceso + entregas faltantes para detectar abandono temprano.

    El problema #1 de una tecnicatura a distancia no es la calidad de la corrección, es la
    deserción — y avisa antes de pasar. Las dos señales ya estaban en el campus y nadie las
    cruzaba: se veía "quién no entregó el TP7" o "quién no entra hace mucho", nunca a la
    misma persona en las dos listas.

    Va EN VIVO (no del caché del snapshot): una alerta de abandono no puede depender de que
    alguien se haya acordado de correr un comando antes. `tareas` en el orden del curso —
    la racha se cuenta desde la última."""
    import asyncio

    alumnos = await _alumnos_de_comision(client, course_id, group_id)
    if isinstance(alumnos, dict):
        return alumnos
    if not alumnos:
        # Comisión sin matriculados (arranque de cuatrimestre). "0 en riesgo" acá NO es
        # "todos al día": es que no hay a quién medir. Decir lo primero deja al tutor sin
        # saber si la herramienta anda o si todavía no hay alumnos.
        return {
            "ok": True, "course_id": course_id, "group_id": group_id,
            "alumnos_totales": 0, "en_riesgo": 0, "rojo": 0, "amarillo": 0, "alumnos": [],
            "sin_alumnos": True,
            "aviso": "Esta comisión todavía no tiene alumnos matriculados: no hay a quién "
                     "evaluar. No es que estén al día.",
            "_meta": {"fuente": "vivo", "tareas_pedidas": len(tareas), "degradado": False},
        }
    if not tareas:
        return {"error": "No tengo tareas mapeadas para este curso, así que no puedo ver "
                         "quién dejó de entregar.",
                "siguiente_paso": "Corré listar_tareas y guardalas con guardar_mis_datos."}

    sem = asyncio.Semaphore(5)

    async def _una(t):
        async with sem:
            return t, await entregas_tarea(client, str(t.get("assign_id")), group_id)

    crudos = await asyncio.gather(*(_una(t) for t in tareas), return_exceptions=True)

    # email -> {assign_id: estado}
    por_alumno: dict[str, dict] = {}
    avisos: list[str] = []
    orden_ok: list[str] = []
    for res in crudos:
        if isinstance(res, BaseException):
            avisos.append(f"Una tarea falló: {type(res).__name__}: {res}")
            continue
        t, r = res
        if r.get("error"):
            avisos.append(f"{t.get('titulo', t.get('assign_id'))}: {r['error']}")
            continue
        aid = str(t.get("assign_id"))
        orden_ok.append(aid)
        for a in r.get("alumnos", []):
            por_alumno.setdefault((a.get("email") or "").lower(), {})[aid] = a.get("estado")

    # Respetar el orden de `tareas`, no el de llegada de las respuestas (van en paralelo).
    orden = [str(t.get("assign_id")) for t in tareas if str(t.get("assign_id")) in orden_ok]

    # SÓLO LO VENCIDO cuenta como no entregado. El `duedate` sale de `_assign_map`, que ya
    # está cacheado: no cuesta ninguna request. Ver `_vencidas` para el porqué.
    import time as _time
    amap = await _assign_map(client)
    orden, no_exigibles = _vencidas(orden, {k: v.get("duedate") for k, v in amap.items()},
                                    int(_time.time()))
    sin_fecha = [a for a in no_exigibles
                 if not int((amap.get(a) or {}).get("duedate") or 0)]
    if no_exigibles:
        avisos.append(
            f"{len(no_exigibles)} de {len(no_exigibles) + len(orden)} actividades NO se "
            f"contaron para la racha porque todavía no vencieron"
            + (f" ({len(sin_fecha)} de ésas no tienen fecha de entrega cargada)"
               if sin_fecha else "")
            + ": una entrega que no venció no puede leerse como abandono.")
    if not orden:
        # No es un detalle: acá el eje de ENTREGAS queda sin medir y el riesgo pasa a
        # apoyarse sólo en el reloj de la materia. Decirlo importa más que el número.
        avisos.append(
            "NINGUNA actividad venció todavía, así que la racha de entregas no se pudo medir "
            "y NO entra en la clasificación: lo que ves sale sólo del reloj de la materia. "
            "Un alumno sin marcar acá no está 'al día con las entregas' — no se sabe.")

    filas = []
    sin_dato_aula = 0
    for u in alumnos:
        email = (u.get("email") or "").lower()
        estados = [por_alumno.get(email, {}).get(aid) for aid in orden]
        estados = [e for e in estados if e is not None]
        racha = _racha_final_sin_entregar(estados)
        estado_aula, dias_aula = _lectura_aula(u)
        dias_campus = _dias_desde(u.get("lastaccess"))
        if estado_aula == _AULA_SIN_DATO:
            sin_dato_aula += 1
        nivel, motivos = _clasificar_riesgo(estado_aula, dias_aula, racha, dias_campus,
                                            dias_alerta, dias_aviso)
        if nivel == "verde":
            continue
        filas.append({
            "nombre": u.get("fullname"),
            "email": email,
            "userid": u.get("id"),
            "nivel": nivel,
            # Los DOS relojes, con nombre propio. El campo ambiguo `dias_sin_entrar` se
            # eliminó a propósito: era del sitio y todo el mundo lo leía como de la materia.
            "estado_aula": estado_aula,
            "dias_sin_abrir_la_materia": dias_aula,
            "dias_sin_entrar_al_campus": dias_campus,
            "tareas_seguidas_sin_entregar": racha,
            "sin_entregar_total": sum(1 for e in estados if e == "Sin entrega"),
            # Sobre cuántas se midió. Sin esto, `racha: 0` con cero vencidas se lee como
            # "viene entregando todo" cuando en realidad es "no había nada que entregar".
            "actividades_vencidas_miradas": len(orden),
            "motivos": motivos,
        })

    # Orden: primero por nivel, y dentro del nivel por días sin abrir LA MATERIA. Los que
    # nunca la abrieron van arriba de su nivel (no tienen número, y no se les inventa uno).
    _peso = {"rojo": 0, "amarillo": 1, "sin_datos": 2}

    def _clave(f):
        if f["estado_aula"] == _AULA_NUNCA:
            return (_peso.get(f["nivel"], 3), 0, 0)
        return (_peso.get(f["nivel"], 3), 1, -(f["dias_sin_abrir_la_materia"] or 0))

    filas.sort(key=_clave)
    rojos = sum(1 for f in filas if f["nivel"] == "rojo")

    return {
        "ok": True,
        "course_id": course_id,
        "group_id": group_id,
        "alumnos_totales": len(alumnos),
        "en_riesgo": len(filas),
        "rojo": rojos,
        "amarillo": sum(1 for f in filas if f["nivel"] == "amarillo"),
        "sin_datos": sum(1 for f in filas if f["nivel"] == "sin_datos"),
        "alumnos": filas,
        "criterio": {
            "reloj": "los días son SIN ABRIR ESTA MATERIA (lastcourseaccess). El acceso al "
                     "campus va aparte, en `dias_sin_entrar_al_campus`, y sirve para saber si "
                     "el alumno eligió no entrar o si no aparece por ningún lado.",
            "rojo": f"2+ tareas seguidas sin entregar · o >{dias_aviso} días sin abrir la "
                    "materia entrando al campus igual (eligió no entrar) · o "
                    f">{dias_alerta} días sin abrirla con al menos 1 sin entregar · o nunca la "
                    "abrió y además dejó de entregar o tampoco pisa el campus",
            "amarillo": f">{dias_aviso} días sin abrir la materia · o la última tarea sin "
                        "entregar · o nunca la abrió pero todavía no venció nada (puede "
                        "haberse matriculado recién)",
            "sin_datos": "no se pudo leer su acceso a la materia. NO es verde: es no sabemos.",
        },
        "_meta": {
            "fuente": "vivo",
            "tareas_revisadas": len(orden),
            "tareas_pedidas": len(tareas),
            "alumnos_sin_dato_de_aula": sin_dato_aula,
            "degradado": bool(avisos) or bool(sin_dato_aula),
            "avisos": avisos + ([f"{sin_dato_aula} alumno(s) vinieron sin el último acceso a "
                                 "la materia: van como `sin_datos`, no como verdes."]
                                if sin_dato_aula else []),
        },
    }


# ---------- DESENGANCHE POR MATERIA: lo que arma la lista y la ordena ----------


def _fila_aula(u: dict, dias_desenganche: int = _AULA_DESENGANCHE_DIAS) -> dict:
    """Una fila de `sin_entrar_al_aula`: los DOS relojes, nombrados sin ambigüedad.

    Van los dos a propósito, porque el del curso solo no alcanza para decidir a quién
    escribir: "hace 10 días que no abre Prog 1" es una cosa si además no aparece por el
    campus, y otra muy distinta si entró ayer para otra materia — ahí no se le perdió la
    contraseña, eligió no entrar. Ese cruce es la señal y ninguna vista del campus la muestra
    junta.

    `entra_al_campus_sin_abrir_la_materia` pide las TRES condiciones, no una:
    1. que esté apareciendo por el campus (entró en los últimos `dias_desenganche` días),
    2. que hace `dias_desenganche` días o más que no abre ESTA materia (o que nunca la abrió),
    3. que haya brecha real entre los dos relojes.

    Sin la (1), el que no aparece por ningún lado —41 días sin pisar el campus, la materia
    nunca abierta— salía etiquetado "está cursando otra cosa", que es falso: ése no está en
    ninguna parte, y es un problema distinto. Sin la (2) se marcaba media comisión."""
    estado, dias_aula = _lectura_aula(u)
    dias_sitio = _dias_desde(u.get("lastaccess"))

    activo_en_campus = dias_sitio is not None and dias_sitio <= dias_desenganche
    desenganchado = estado == _AULA_NUNCA or (
        estado == _AULA_ABRIO and dias_aula >= dias_desenganche)
    hay_brecha = estado == _AULA_NUNCA or (
        estado == _AULA_ABRIO and dias_sitio is not None
        and dias_aula - dias_sitio >= _BRECHA_MINIMA_DIAS)
    elige_no_entrar = activo_en_campus and desenganchado and hay_brecha

    return {
        "nombre": u.get("fullname"),
        "email": (u.get("email") or "").lower(),
        "userid": u.get("id"),
        "estado_aula": estado,
        "dias_sin_abrir_la_materia": dias_aula,
        "dias_sin_entrar_al_campus": dias_sitio,
        # Los timestamps crudos, además de los días. Una fecha ("~24 jun 2025") es más concreta
        # que "414 d" para quien tiene que escribirle al alumno, y el informe no puede
        # reconstruirla desde los días sin reintroducir el redondeo. 0 = nunca.
        "ultimo_acceso_aula_ts": int(u.get("lastcourseaccess") or 0),
        "ultimo_acceso_campus_ts": int(u.get("lastaccess") or 0),
        "desenganchado_de_la_materia": desenganchado,
        "entra_al_campus_sin_abrir_la_materia": elige_no_entrar,
        "detalle": _detalle_aula(estado, dias_aula, dias_sitio, elige_no_entrar),
    }


def _detalle_aula(estado: str, dias_aula: int | None, dias_sitio: int | None,
                  elige_no_entrar: bool) -> str:
    """La fila en una frase, para que el tutor no tenga que interpretar dos números."""
    if estado == _AULA_SIN_DATO:
        return ("El campus no devolvió su último acceso a la materia: no se pudo relevar. "
                "NO quiere decir que no la haya abierto.")
    if estado == _AULA_NUNCA:
        if dias_sitio is None:
            return "Nunca abrió la materia y nunca entró al campus tampoco."
        if elige_no_entrar:
            return (f"Nunca abrió la materia, y entró al campus hace {dias_sitio} día(s): "
                    "está cursando otra cosa, no perdió el acceso.")
        return (f"Nunca abrió la materia, y hace {dias_sitio} día(s) que tampoco entra al "
                "campus: no aparece por ningún lado.")
    if elige_no_entrar:
        return (f"Hace {dias_aula} día(s) que no abre la materia, pero entró al campus hace "
                f"{dias_sitio}: está activo en otra materia.")
    return f"Hace {dias_aula} día(s) que no abre la materia."


_ORDEN_AULA = {_AULA_NUNCA: 0, _AULA_ABRIO: 1, _AULA_SIN_DATO: 2}


def _orden_aula(f: dict) -> tuple:
    """Los que nunca la abrieron arriba, después por días sin abrirla, y los `sin_dato` al
    final — separados a propósito, porque no son un caso peor ni mejor: son un no-dato, y
    mezclarlos entre los medidos los haría pasar por medidos.

    Entre los que nunca abrieron va primero el que MÁS reciente entró al campus: ése es el
    caso que esta tool existe para encontrar. El que nunca entró ni al campus queda al final
    del grupo, pero se cuenta aparte en la salida para que no se pierda de vista."""
    estado = f["estado_aula"]
    if estado == _AULA_NUNCA:
        dias = f.get("dias_sin_entrar_al_campus")
        return (_ORDEN_AULA[estado], dias if dias is not None else 10 ** 9)
    if estado == _AULA_ABRIO:
        return (_ORDEN_AULA[estado], -(f.get("dias_sin_abrir_la_materia") or 0))
    return (_ORDEN_AULA[estado], 0)


async def sin_entrar_al_aula(client, course_id: int, group_id: int,
                             dias_desenganche: int = _AULA_DESENGANCHE_DIAS) -> dict:
    """Quién dejó de abrir ESTA materia, ordenado por hace cuánto.

    Es la única señal de desenganche POR MATERIA que tiene la skill. `alumnos_en_riesgo` no
    la cubre: cuenta rachas de actividades sin entregar, y al principio del cuatrimestre —
    cuando todavía no venció nada — devuelve el padrón entero en rojo (visto 35/35 y 69/69).
    El reloj del curso, en cambio, sirve desde el día uno y no depende de que haya fechas
    de entrega vencidas."""
    alumnos = await _alumnos_de_comision(client, course_id, group_id)
    if isinstance(alumnos, dict):  # error de la comisión: se devuelve, no se traga
        return alumnos
    if not alumnos:
        # Mismo criterio que `alumnos_en_riesgo`: "0 desenganchados" acá NO es "están todos
        # entrando", es que no hay a quién medir.
        return {
            "ok": True, "course_id": course_id, "group_id": group_id,
            "alumnos_totales": 0, "nunca_abrieron": 0, "sin_dato": 0, "alumnos": [],
            "desenganchados": 0, "entran_al_campus_sin_abrir_la_materia": 0,
            "nunca_entraron_ni_al_campus": 0,
            "sin_alumnos": True,
            "aviso": "Esta comisión todavía no tiene alumnos matriculados: no hay a quién "
                     "medir. No es que estén entrando todos.",
            "_meta": {"fuente": "vivo", "campo_disponible": None, "degradado": False},
        }

    filas = sorted((_fila_aula(u, dias_desenganche) for u in alumnos), key=_orden_aula)
    sin_dato = [f for f in filas if f["estado_aula"] == _AULA_SIN_DATO]
    nunca = [f for f in filas if f["estado_aula"] == _AULA_NUNCA]
    abrieron = [f for f in filas if f["estado_aula"] == _AULA_ABRIO]

    if len(sin_dato) == len(filas):
        # El escenario de permisos. Devolver una lista de ceros acá sería lo peor que puede
        # hacer esta tool: se leería como "todos abrieron la materia hoy".
        return {
            "error": (f"El campus no devolvió el último acceso a la materia para NINGUNO de "
                      f"los {len(filas)} alumnos, así que no pude relevar nada. Ojo: eso NO "
                      "significa que no la hayan abierto."),
            "causa_probable": ("El sitio tiene 'lastaccess' en hiddenuserfields y tu rol no "
                               "puede ver campos ocultos (moodle/course:viewhiddenuserfields). "
                               "Es el mismo permiso que tapa `dias_sin_entrar`."),
            "course_id": course_id, "group_id": group_id,
            "_meta": {"fuente": "vivo", "campo_disponible": False, "degradado": True},
        }

    mas_atrasado = abrieron[0] if abrieron else None
    desenganchados = [f for f in filas if f["desenganchado_de_la_materia"]]
    activos_sin_abrir = [f for f in filas if f["entra_al_campus_sin_abrir_la_materia"]]
    nunca_ni_campus = [f for f in nunca if f["dias_sin_entrar_al_campus"] is None]

    if activos_sin_abrir:
        resumen = (f"{len(activos_sin_abrir)} de {len(filas)} alumnos entran al campus pero "
                   f"hace {dias_desenganche}+ días que no abren esta materia: son los que hay "
                   "que contactar, y son justo los que `dias_sin_entrar` mostraba como si "
                   "estuvieran al día.")
    elif desenganchados:
        resumen = (f"{len(desenganchados)} de {len(filas)} hace {dias_desenganche}+ días que "
                   "no abren la materia (o nunca la abrieron), pero ninguno de ellos está "
                   "entrando al campus: no es que elijan no entrar, no aparecen por ningún "
                   "lado.")
    else:
        # Nunca un cero mudo: si están todos entrando, se dice con el número del peor. Y se
        # cuenta sólo a los MEDIDOS — decir "los 4 abrieron la materia" cuando 2 vinieron sin
        # el dato es el mismo cero mudo con otra cara: se lee como "está todo al día".
        resumen = (f"Los {len(abrieron)} alumnos relevados abrieron la materia y ninguno hace "
                   f"{dias_desenganche}+ días que no la abre; el más atrasado hace "
                   f"{mas_atrasado['dias_sin_abrir_la_materia']} día(s).")
        if sin_dato:
            resumen += (f" OJO: {len(sin_dato)} de {len(filas)} quedaron sin relevar, así que "
                        "esto NO cubre a toda la comisión.")

    salida = {
        "ok": True,
        "course_id": course_id,
        "group_id": group_id,
        "alumnos_totales": len(filas),
        "desenganchados": len(desenganchados),
        "nunca_abrieron": len(nunca),
        "nunca_entraron_ni_al_campus": len(nunca_ni_campus),
        "entran_al_campus_sin_abrir_la_materia": len(activos_sin_abrir),
        "sin_dato": len(sin_dato),
        "alumnos": filas,
        "resumen": resumen,
        "criterio": {
            "orden": "Primero los que nunca la abrieron, después por días sin abrirla "
                     "(el más viejo arriba). Los `sin_dato` van al final, aparte.",
            "reloj": "días sin abrir ESTA materia (lastcourseaccess), NO días sin entrar al "
                     "campus (lastaccess). Los dos vienen en cada fila.",
            "desenganchado": f"{dias_desenganche}+ días sin abrir la materia, o nunca abierta.",
            "a_quien_contactar": (f"`entra_al_campus_sin_abrir_la_materia`: entró al campus en "
                                  f"los últimos {dias_desenganche} días Y hace "
                                  f"{dias_desenganche}+ que no abre esta materia. Ése eligió "
                                  "no entrar. El que no pisa el campus tampoco es otro "
                                  "problema y no se mezcla."),
        },
        "_meta": {
            "fuente": "vivo",
            "campo_disponible": True,
            "degradado": bool(sin_dato),
            "avisos": ([f"{len(sin_dato)} de {len(filas)} alumnos vinieron sin el último "
                        "acceso a la materia: quedaron como `sin_dato`, no como 'nunca "
                        "abrió'. El relevamiento está incompleto."] if sin_dato else []),
        },
    }
    if sin_dato:
        salida["aviso"] = salida["_meta"]["avisos"][0]
    return salida


async def traza_alumno(client, alumno: dict, tareas: list[dict]) -> dict:
    """Qué entregó y qué nota sacó UN alumno, tarea por tarea, en vivo.

    Es la parte cara de `buscar_alumno` (dos requests por tarea), por eso va aparte y
    bajo demanda: para saber quién es alguien y en qué comisión está no hace falta.
    Reusa `entregas_tarea`, así la nota se lee por texto de la escala (respeta el
    Aprobado/Desaprobado invertido) en vez de reimplementar esa lógica acá."""
    import asyncio

    email = (alumno.get("email") or "").lower()
    group_id = alumno.get("group_id") or 0
    if not email or not tareas:
        return {"entregas": [], "_meta": {"fuente": "vivo", "tareas_revisadas": 0}}

    sem = asyncio.Semaphore(5)

    async def _una(t):
        async with sem:
            res = await entregas_tarea(client, str(t.get("assign_id")), group_id)
            if isinstance(res, dict) and res.get("error"):
                return {"titulo": t.get("titulo", ""), "error": res["error"]}
            fila = next(
                (a for a in res.get("alumnos", []) if (a.get("email") or "").lower() == email),
                None,
            )
            if fila is None:
                return None  # no figura en esa tarea (no está habilitado): no es un dato
            return {
                "tarea": res.get("tarea") or t.get("titulo", ""),
                "assign_id": str(t.get("assign_id")),
                "estado": fila.get("estado"),
                "nota": fila.get("nota"),
                "pendiente": fila.get("pendiente"),
            }

    crudos = await asyncio.gather(*(_una(t) for t in tareas), return_exceptions=True)

    entregas, avisos = [], []
    for r in crudos:
        if isinstance(r, BaseException):
            avisos.append(f"Una tarea falló: {type(r).__name__}: {r}")
        elif r is None:
            continue
        elif r.get("error"):
            avisos.append(f"{r['titulo']}: {r['error']}")
        else:
            entregas.append(r)

    return {
        "entregas": entregas,
        "sin_entrega": [e["tarea"] for e in entregas if e["estado"] == "Sin entrega"],
        "pendientes_de_correccion": [e["tarea"] for e in entregas if e.get("pendiente")],
        "_meta": {
            "fuente": "vivo",
            "tareas_revisadas": len(entregas),
            "tareas_pedidas": len(tareas),
            "degradado": bool(avisos),
            "avisos": avisos,
        },
    }


# ---------- Adjuntar un archivo a la devolución ----------

async def _subir_a_draft(client, ruta: str) -> tuple[int | None, str | None]:
    """Sube un archivo al área `draft` del tutor y devuelve `(itemid, None)`.

    NO es un web service: Moodle expone `/webservice/upload.php`, que recibe el token
    como campo del formulario. El `itemid` que devuelve es lo que después se pasa en el
    `plugindata` de `mod_assign_save_grade` para que el archivo quede adjunto a la
    devolución del alumno. Ante error devuelve `(None, motivo)`."""
    import os as _os

    import httpx  # local: este módulo no usa httpx en ningún otro lado

    if not _os.path.isfile(ruta):
        return None, f"No encontré el archivo {ruta}"
    await client.ws("core_webservice_get_site_info")   # asegura que haya token
    token = getattr(client, "_token", None)
    if not token:
        return None, "No pude obtener el token para subir el archivo."
    nombre = _os.path.basename(ruta)
    try:
        with open(ruta, "rb") as fh:
            contenido = fh.read()
        async with httpx.AsyncClient(timeout=120.0) as http:
            resp = await http.post(
                f"{client._base}/webservice/upload.php",
                data={"token": token, "filearea": "draft", "itemid": 0},
                files={"file_1": (nombre, contenido, "application/octet-stream")},
            )
    except (OSError, httpx.HTTPError) as e:
        return None, f"No pude subir {nombre}: {e}"
    if resp.status_code != 200:
        return None, f"upload.php devolvió {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return None, f"upload.php no devolvió JSON: {resp.text[:200]}"
    # Ante error, Moodle responde un dict con `error`; en el caso bueno, una lista.
    if isinstance(data, dict) and data.get("error"):
        return None, f"upload.php: {data.get('error')}"
    if not isinstance(data, list) or not data:
        return None, f"upload.php devolvió algo inesperado: {str(data)[:200]}"
    return data[0].get("itemid"), None


# ---------- ESCRITURA de nota (mod_assign_save_grade) ----------

def _resolver_valor_nota(grade_cfg, nota) -> tuple[float | None, str | None, list | None]:
    """Traduce la nota pedida al valor que espera save_grade.
    - grade_cfg < 0 → ESCALA (-scaleid): 'Aprobado'/'Desaprobado' -> índice (1/2).
    - grade_cfg ≥ 0 → NUMÉRICA: se usa el número tal cual (coma o punto).
    Devuelve (valor, etiqueta, opciones_validas | None). valor None = no resuelto."""
    # ANTE LA DUDA, NO SE ADIVINA. Antes esto hacía `gc = 0` (→ numérica) cuando el valor
    # venía None o ilegible, y numérica es justamente el modo que corrompe: en una tarea
    # con escala, mandar "1" pensando en la peor nota guarda APROBADO (la escala 5 va
    # invertida). Preferimos negarnos a cargar antes que escribir al revés en un legajo.
    try:
        gc = int(grade_cfg)
    except (TypeError, ValueError):
        return None, None, [
            "(no pude determinar si esta tarea usa escala o nota numérica — "
            "no cargo nada a ciegas; verificá el grader en el campus)"
        ]
    if gc < 0:
        scaleid = -gc
        mapa = _ESCALAS.get(scaleid)
        if mapa is None:
            return None, None, ["(escala desconocida id %d)" % scaleid]
        idx = mapa.get(_norm(nota))
        if idx is None:
            # ¿pasaron el índice directo?
            try:
                if int(float(str(nota).replace(",", "."))) in mapa.values():
                    idx = int(float(str(nota).replace(",", ".")))
            except (TypeError, ValueError):
                idx = None
        if idx is None:
            etiquetas = [k.capitalize() for k in mapa]
            return None, None, etiquetas
        etiqueta = next((k.capitalize() for k, v in mapa.items() if v == idx), str(idx))
        return float(idx), etiqueta, None
    # numérica
    try:
        val = float(str(nota).replace(",", "."))
    except (TypeError, ValueError):
        return None, None, ["(número, ej. 9.5)"]
    return val, str(nota), None


def nota_display(grade_cfg, grade_value) -> str | None:
    """Convierte el valor crudo de una nota (get_grades) a texto legible según el tipo:
    escala → 'Aprobado'/'Desaprobado'; numérica → el número limpio. None si no hay nota.

    Ante una escala DESCONOCIDA no se devuelve el número crudo. En la escala 5 de la TUP
    el 1 es "Aprobado" y el 2 "Desaprobado" — van invertidos respecto de lo intuitivo —,
    así que mostrar un índice suelto de otra escala se puede leer exactamente al revés:
    alguien vería "1" y entendería "la peor nota". Vale mucho más un aviso feo que un
    número que miente."""
    if grade_value in (None, "", "-1.00000", "-1"):
        return None
    try:
        gc = int(grade_cfg)
    except (TypeError, ValueError):
        return (f"⚠️ no sé si esta tarea usa escala o nota numérica "
                f"(valor crudo: {grade_value}) — confirmá en el campus")
    if gc < 0:
        scaleid = -gc
        mapa = _ESCALAS.get(scaleid)
        if mapa is None:
            return (f"⚠️ escala {scaleid} desconocida (valor crudo: {grade_value}) — "
                    "no la interpreto, confirmá en el campus")
        try:
            idx = int(float(str(grade_value).replace(",", ".")))
        except (TypeError, ValueError):
            return f"⚠️ valor ilegible en escala {scaleid}: {grade_value!r}"
        for k, v in mapa.items():
            if v == idx:
                return k.capitalize()
        return (f"⚠️ el valor {idx} no está en la escala {scaleid} "
                f"(esperaba: {', '.join(str(v) for v in sorted(mapa.values()))})")
    try:
        f = float(str(grade_value).replace(",", "."))
        return str(int(f)) if f == int(f) else f"{f:g}"
    except (TypeError, ValueError):
        return str(grade_value)


async def cargar_nota(client, cmid: str, email: str, nota, mensaje: str,
                      confirmado: bool = False, adjunto: str | None = None) -> dict:
    """Escribe nota + devolución por API REST (mod_assign_save_grade). Sin navegador.
    Escala (TPs): pasar 'Aprobado'/'Desaprobado'. Numérica (TIO): el número. Verifica
    con get_grades que la nota quedó guardada."""
    # refrescar=True a propósito: esto escribe en el legajo de una persona, y el tipo de
    # calificación de una tarea cambia mientras el aula se arma. Un caché de hace horas
    # acá vale una nota invertida (ver _assign_map).
    amap = await _assign_map(client, refrescar=True)
    cfg = amap.get(str(cmid))
    if cfg is None:
        return {"error": f"No encontré la tarea cmid={cmid} en tus cursos."}
    gcfg = cfg.get("grade")
    valor, etiqueta, opciones = _resolver_valor_nota(gcfg, nota)
    if valor is None:
        # `es_escala` sólo se afirma cuando de verdad se pudo leer la config. Antes
        # `cfg.get("grade", 0) < 0` devolvía False tanto para "es numérica" como para "no
        # sé", y esas dos cosas no son lo mismo.
        try:
            es_escala = int(gcfg) < 0
        except (TypeError, ValueError):
            es_escala = None
        return {"error": f"'{nota}' no es una nota válida para esta tarea.",
                "es_escala": es_escala, "opciones": opciones}
    uid, nombre = await _userid_por_email(client, email)
    if uid is None:
        return {"error": f"No encontré al alumno {email} (¿entregó / está en el curso?)."}
    if not confirmado:
        return {"requiere_confirmacion": True,
                "preview": {"alumno": nombre or email, "nota": etiqueta, "mensaje": mensaje},
                "aviso": "Llamá de nuevo con confirmado=true para escribir en Moodle."}

    inst = cfg["id"]
    html_msg = "".join(f"<p>{_html.escape(p)}</p>" for p in str(mensaje).split("\n") if p.strip())
    # GOTCHA: mod_assign_save_grade EXIGE plugindata (la devolución) presente y con
    # contenido; sin ella tira dmlwriteexception. Si no hay mensaje, mandamos al menos
    # la etiqueta de la nota para que el write no falle.
    if not html_msg:
        html_msg = f"<p>{_html.escape(str(etiqueta))}</p>"

    plugindata: dict = {"assignfeedbackcomments_editor": {"text": html_msg, "format": 1}}
    adjunto_aviso = None
    if adjunto:
        acepta_adjuntos = cfg.get("feedback_file")
        if acepta_adjuntos is False:
            # La tarea NO tiene habilitado el plugin de archivos de retroalimentación.
            # Moodle acepta el itemid y lo DESCARTA sin error, así que subirlo daría un
            # éxito falso: el alumno leería "te adjunto el PDF" y no habría nada.
            adjunto_aviso = (
                "Esta tarea NO acepta archivos de retroalimentación (el plugin "
                "'assignfeedback_file' está deshabilitado), así que el adjunto no se subió. "
                "Moodle lo habría ignorado en silencio. Si el mensaje promete un archivo, "
                "sacá esa frase o pedile a la cátedra que habilite el plugin."
            )
        elif acepta_adjuntos is None:
            # No pudimos leer la config de la tarea. Eso NO es "está deshabilitado": es
            # que no sabemos. No se sube a ciegas, pero tampoco se afirma una causa falsa.
            adjunto_aviso = (
                "No pude leer la configuración de la tarea, así que no sé si acepta "
                "archivos de retroalimentación — no subí el adjunto a ciegas. Verificá el "
                "grader en el campus; si el mensaje promete un archivo, sacá esa frase."
            )
        else:
            itemid, motivo = await _subir_a_draft(client, adjunto)
            if itemid is None:
                # El adjunto que falla NO tumba la nota: se carga igual y se avisa. Pero
                # se avisa fuerte, porque la devolución suele PROMETER el archivo.
                adjunto_aviso = f"La nota se cargó, pero el adjunto NO se subió: {motivo}"
            else:
                plugindata["files_filemanager"] = itemid

    try:
        await client.ws("mod_assign_save_grade", {
            "assignmentid": inst, "userid": uid, "grade": valor, "attemptnumber": -1,
            "addattempt": 0, "workflowstate": "", "applytoall": 1,
            "plugindata": plugindata,
        })
    except MoodleWSError as e:
        return {"error": f"No pude guardar la nota: {e.errorcode} — {e.message}"}

    # Verificación: releer la nota por API. Son TRES desenlaces, no dos —
    # guardada / NO guardada / no se pudo comprobar — y colapsarlos en un bool hacía que
    # "no pude leer" se leyera igual que "no quedó guardada".
    verificado: bool | None = None      # None = no se pudo comprobar
    leido = None
    try:
        g = await client.ws("mod_assign_get_grades", {"assignmentids": [inst]})
        for asg in (g or {}).get("assignments", []):
            for gr in asg.get("grades", []):
                if int(gr.get("userid") or -1) == uid:
                    leido = gr.get("grade")
                    try:
                        verificado = abs(float(leido) - valor) < 0.001
                    except (TypeError, ValueError):
                        # Sin nota legible (Moodle usa -1 para "sin calificar").
                        verificado = False
    except MoodleWSError as e:
        log.warning("No pude releer la nota de %s: %s", email, e)

    if verificado is False:
        # Moodle aceptó el write pero la nota NO quedó. Devolver ok=True acá fue el
        # agujero: la tool YA detectaba la falla y el "ok" al lado la hacía pasar por
        # éxito. Un fallo se reporta como fallo.
        return {
            "ok": False,
            "error": "Moodle aceptó la escritura pero la nota NO quedó guardada.",
            "alumno": nombre or email,
            "nota_pedida": etiqueta,
            "nota_en_moodle": nota_display(cfg.get("grade"), leido),
            "devolucion_escrita": True,
            "siguiente_paso": "La devolución sí se guardó, así que el alumno figura como "
                              "corregido SIN nota. Reintentá o cargala desde el grader.",
        }

    salida = {"ok": True, "alumno": nombre or email, "nota": etiqueta, "verificado": verificado}
    if verificado is None:
        salida["aviso"] = ("La nota se escribió, pero no pude releerla para confirmarlo. "
                           "Verificala con entregas_tarea antes de darla por cargada.")
    if adjunto:
        # Se informa SIEMPRE, haya salido bien o mal: el texto de la devolución suele
        # decir "te adjunto el PDF", y un adjunto que falló en silencio deja al alumno
        # buscando un archivo que no está.
        salida["adjunto"] = adjunto if adjunto_aviso is None else None
        if adjunto_aviso:
            salida["adjunto_aviso"] = adjunto_aviso
    return salida


# ---------- FOROS (JSON, reemplaza el scraping) ----------

async def listar_foros(client, course_id: int) -> dict:
    try:
        fs = await client.ws("mod_forum_get_forums_by_courses", {"courseids": [course_id]})
    except MoodleWSError as e:
        return {"error": f"No pude listar foros: {e.errorcode}"}
    foros = [{"forum_id": f.get("id"), "nombre": _plano(f.get("name", ""), 80),
              "discusiones": f.get("numdiscussions"), "cmid": f.get("cmid"), "tipo": f.get("type")}
             for f in (fs or [])]
    if not foros:
        return {"error": "No encontré foros en el curso.", "course_id": course_id}
    return {"ok": True, "course_id": course_id, "foros": foros}


async def leer_foro(client, forum_id: int, limite: int = 25) -> dict:
    try:
        d = await client.ws("mod_forum_get_forum_discussions", {"forumid": forum_id})
    except MoodleWSError as e:
        return {"error": f"No pude abrir el foro {forum_id}: {e.errorcode}"}
    disc = [{"discussion_id": x.get("discussion"), "titulo": _plano(x.get("name", ""), 120),
             "autor": x.get("userfullname"),
             # El userid del que abrió el hilo: es lo ÚNICO que permite saber si una
             # consulta es de un alumno propio. El groupid no sirve — en este campus los
             # foros de consulta son del curso entero y vienen todos con -1.
             "autor_userid": x.get("userid"),
             "replicas": x.get("numreplies", 0),
             "ultimo_ts": x.get("timemodified"), "fijada": bool(x.get("pinned")),
             "group_id": x.get("groupid"),
             "puede_responder": bool(x.get("canreply"))}
            for x in (d or {}).get("discussions", [])]
    return {"ok": True, "forum_id": forum_id, "total": len(disc), "discusiones": disc[: max(1, limite)]}


async def leer_discusion(client, discussion_id: int) -> dict:
    try:
        d = await client.ws("mod_forum_get_discussion_posts", {"discussionid": discussion_id})
    except MoodleWSError as e:
        return {"error": f"No pude abrir la discusión {discussion_id}: {e.errorcode}"}
    posts = []
    for p in (d or {}).get("posts", []):
        autor = p.get("author") or {}
        posts.append({
            "post_id": p.get("id"), "autor": autor.get("fullname"),
            "autor_userid": autor.get("id"), "fecha_ts": p.get("timecreated"),
            "asunto": _plano(p.get("subject", ""), 120), "texto": _plano(p.get("message", "")),
            "responde_a_post": p.get("parentid") or None,
        })
    # Cronológico: el primer post es la consulta/aviso original, después las respuestas.
    posts.sort(key=lambda x: x.get("fecha_ts") or 0)
    if not posts:
        return {"error": f"No encontré posts en la discusión {discussion_id}.", "discussion_id": discussion_id}
    return {"ok": True, "discussion_id": discussion_id, "posts": posts}


async def responder_foro(client, post_id: int, mensaje: str, asunto: str | None = None,
                         confirmado: bool = False) -> dict:
    """Responde a un post de un foro (mod_forum_add_discussion_post). Escritura: preview
    primero, y con OK del tutor confirmado=true."""
    if not confirmado:
        return {"requiere_confirmacion": True, "preview": {"responde_a_post": post_id, "texto": mensaje},
                "aviso": "Confirmá (confirmado=true) para publicar la respuesta en el foro."}
    params = {"postid": post_id, "subject": asunto or "Re:", "message": mensaje,
              "messageformat": 1}
    try:
        r = await client.ws("mod_forum_add_discussion_post", params)
    except MoodleWSError as e:
        return {"error": f"No pude publicar la respuesta: {e.errorcode} — {e.message}"}
    return {"ok": True, "post_id": (r or {}).get("postid"), "responde_a": post_id}


def _a_html(texto: str) -> str:
    """Texto plano -> HTML simple para el foro. Moodle guarda el mensaje con
    messageformat=1 (HTML): si le mandás saltos de línea crudos, el campus los come y el
    aviso queda como un ladrillo de una sola línea. Cada párrafo va en su <p>.

    El texto se ESCAPA, igual que en `cargar_nota`. Esto es un foro de **programación**:
    un aviso que diga `if (a < b)` o `List<int>` se renderiza mutilado si el campus lo lee
    como markup, y el tutor no se entera hasta que lo ve publicado."""
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", (texto or "").strip()) if p.strip()]
    return "".join("<p>" + _html.escape(p).replace("\n", "<br>") + "</p>"
                   for p in parrafos)


# Modos de grupo de Moodle: 0 = sin grupos, 1 = separados, 2 = visibles. En 1 y 2 el
# `groupid` del post decide quién lo ve; en 0 el foro es del curso entero y no hay a quién
# dirigirlo. El 2 cuenta igual que el 1 para esto: "visibles" es quién PUEDE leer, no a
# quién se publica.
_GROUPMODES_CON_GRUPOS = (1, 2)


async def _contar_alumnos(client, course_id: int, group_id: int | None = None) -> int | None:
    """Cuántos ALUMNOS hay en el curso, o en uno de sus grupos. `None` = no se pudo saber.

    NO se usa `core_group_get_group_members`: en este campus el rol de tutor no lo tiene
    habilitado y tira `accessexception`. La lista de matriculados sí está permitida y
    además trae los roles, así que el conteo son alumnos y no incluye al propio tutor.

    Va con `onlyactive: 1` como el resto de los caminos de matrícula: era el único que
    contaba también a los suspendidos. Importa porque este número decide el ALCANCE que se le
    muestra al tutor antes de publicar en un foro — decirle "esto lo van a ver 37 personas"
    cuando son 35 activas es prometer de más sobre una escritura que no se deshace."""
    params: dict = {"courseid": course_id}
    params["options[0][name]"] = "onlyactive"
    params["options[0][value]"] = 1
    if group_id:
        params["options[1][name]"] = "groupid"
        params["options[1][value]"] = group_id
    try:
        us = await client.ws("core_enrol_get_enrolled_users", params)
    except MoodleWSError:
        return None
    if not isinstance(us, list):
        return None
    return len([u for u in us if _es_estudiante(u)])


async def crear_discusion(client, forum_id: int, asunto: str, mensaje: str,
                          group_id: int | None = None, confirmado: bool = False) -> dict:
    """Abre un tema NUEVO en un foro (mod_forum_add_discussion). Distinto de
    `responder_foro`, que cuelga de un post existente: esto es lo que se usa para un
    aviso o una bienvenida, donde todavía no hay hilo del que colgarse.

    El `group_id` decide QUIÉN LO VE. En los foros de "Avisos de la comisión"
    (groupmode 1 o 2) con el id de la comisión lo ven sus alumnos; con `0` se publica al
    curso ENTERO. Un aviso de comisión que sale a 500 alumnos ajenos **no se deshace desde
    la API**, así que acá no alcanza con avisar: si no se puede determinar a cuánta gente
    llega, esta función **se niega a publicar**.

    Tres valores distintos, a propósito:
      - `None` (default) → "no me lo dijiste". En un foro con grupos, se rechaza.
      - un id de comisión → va a esa comisión, validado en vivo contra el curso.
      - `0` EXPLÍCITO → "sí, quiero el curso entero". Se permite, y el preview dice a
        cuántos alcanza.

    ESCRITURA — llamala primero SIN `confirmado` para ver el preview (incluye a qué grupo
    y a cuánta gente llega, verificado en vivo). Solo con OK del tutor, confirmado=true.
    """
    avisos: list[str] = []

    # Resolver el foro para saber su groupmode y su curso. Esto NO es decoración del
    # preview: es lo que determina el alcance. Si falla, no sabemos a cuántos les llega.
    ctx: dict = {}
    ctx_ok = True
    try:
        cm = await client.ws("core_course_get_course_module_by_instance",
                             {"module": "forum", "instance": forum_id})
        info = (cm or {}).get("cm") or {}
        ctx = {"foro": _plano(info.get("name", ""), 80), "cmid": info.get("id"),
               "course_id": info.get("course"), "groupmode": info.get("groupmode")}
        if ctx.get("course_id") is None or ctx.get("groupmode") is None:
            ctx_ok = False
    except MoodleWSError as e:
        ctx_ok = False
        avisos.append(f"No pude leer la configuración del foro {forum_id}: {e.errorcode}")
    if not ctx_ok and not avisos:
        avisos.append(f"El campus no devolvió curso/modo de grupo del foro {forum_id}.")

    # ¿Tenemos permiso? Mejor enterarse en el preview que después de que el tutor dijo OK.
    try:
        permiso = await client.ws("mod_forum_can_add_discussion", {"forumid": forum_id})
        if not (permiso or {}).get("status"):
            return {"error": f"No tenés permiso para abrir tema en el foro {forum_id}."}
    except MoodleWSError as e:
        return {"error": f"No pude verificar permisos en el foro {forum_id}: {e.errorcode}"}

    con_grupos = ctx.get("groupmode") in _GROUPMODES_CON_GRUPOS
    destino: dict = {"group_id": group_id,
                     "alcance": "comisión" if group_id else "curso entero"}
    bloqueo: str | None = None

    if not ctx_ok:
        # No sabemos si el foro tiene grupos ni de qué curso es: no se puede afirmar a
        # cuánta gente llega. "No sé" no puede publicarse como si fuera "no hay problema".
        bloqueo = ("No pude determinar el alcance de este foro (no sé si tiene grupos "
                   "separados ni a qué curso pertenece), así que no publico: un aviso de "
                   "comisión que sale al curso entero no se puede borrar desde la API. "
                   "Verificá el foro en el campus o reintentá.")
    elif group_id is None and con_grupos:
        bloqueo = (f"El foro '{ctx.get('foro')}' tiene GRUPOS SEPARADOS y no pasaste "
                   "group_id: el aviso saldría para el CURSO ENTERO. Pasá el group_id de "
                   "tu comisión (sacalo de `mis_datos` o `descubrir_comisiones`), o "
                   "group_id=0 si de verdad querés que lo vea todo el curso.")
    elif group_id:
        # ID validado contra el campus, como manda la Regla de Oro. No es opcional:
        # publicar en el grupo equivocado tiene el mismo costo que publicar de más.
        try:
            gs = await client.ws("core_group_get_course_groups",
                                 {"courseid": ctx["course_id"]})
        except MoodleWSError as e:
            return {"error": f"No pude validar el group_id {group_id} contra el curso "
                             f"{ctx['course_id']} ({e.errorcode}), así que no publico."}
        g = next((x for x in (gs or []) if x.get("id") == group_id), None)
        if g is None:
            return {"error": f"El group_id {group_id} no existe en el curso "
                             f"{ctx['course_id']}. Sacá el id de descubrir_comisiones."}
        destino["grupo_nombre"] = g.get("name")
        if not con_grupos:
            avisos.append(
                f"Pasaste group_id pero el foro '{ctx.get('foro')}' NO tiene grupos "
                "(groupmode=0): Moodle lo ignora y el aviso lo va a ver el curso entero.")
            destino["alcance"] = "curso entero"

    # A cuánta gente llega, verificado en vivo. Es EL número del preview.
    if ctx.get("course_id") is not None:
        alcance_grupo = group_id if destino["alcance"] == "comisión" else None
        n = await _contar_alumnos(client, ctx["course_id"], alcance_grupo)
        destino["alcance_alumnos"] = n
        if n is None:
            avisos.append("No pude contar a cuántos alumnos llega — el número de abajo "
                          "es desconocido, no cero.")

    if bloqueo:
        return {"error": bloqueo, "foro": ctx.get("foro"), "forum_id": forum_id,
                "groupmode": ctx.get("groupmode"),
                "_meta": {"fuente": "vivo", "degradado": not ctx_ok, "avisos": avisos}}

    if not confirmado:
        return {"requiere_confirmacion": True,
                "preview": {"foro": ctx.get("foro"), "forum_id": forum_id,
                            "asunto": asunto, "destino": destino, "texto": mensaje},
                "aviso": "Confirmá (confirmado=true) para publicar el tema en el foro.",
                "_meta": {"fuente": "vivo", "degradado": False, "avisos": avisos}}

    params = {"forumid": forum_id, "subject": asunto, "message": _a_html(mensaje),
              "options[0][name]": "discussionsubscribe", "options[0][value]": 1}
    if group_id:
        params["groupid"] = group_id
    try:
        r = await client.ws("mod_forum_add_discussion", params)
    except MoodleWSError as e:
        return {"error": f"No pude publicar el tema: {e.errorcode} — {e.message}"}
    return {"ok": True, "discussion_id": (r or {}).get("discussionid"),
            "forum_id": forum_id, "asunto": asunto, **destino,
            "nota": "Quedaste suscripto a la discusión (te llegan las respuestas por mail).",
            "_meta": {"fuente": "vivo", "degradado": False, "avisos": avisos}}


_RE_CONSULTAS = re.compile(r"consulta|duda", re.I)
# Foros que NO son para que conteste el tutor, aunque digan "consulta": el de buscar
# dupla es alumno-con-alumno (200+ hilos de "busco compañero" que nadie debe responder).
_RE_NO_DOCENTE = re.compile(r"dupla|compañer|equipo", re.I)


async def foros_pendientes(client, course_id: int, group_ids: list[int] | None = None,
                           solo_consultas: bool = True, incluir_avisos: bool = False,
                           max_discusiones: int = 120) -> dict:
    """Consultas de foro que el tutor todavía no respondió, en todo el curso.

    Dos categorías, porque no son lo mismo y conviene atacarlas distinto:
      - `sin_responder`: nadie contestó (0 réplicas). Son las urgentes.
      - `respondio_otro`: alguien contestó, pero el tutor no. Puede estar resuelta por un
        compañero o necesitar que un docente confirme.

    Criterio de "respondida por vos": hay al menos un post cuyo autor es el userid del
    tutor logueado. NO se infiere por rol — el WS de foros no devuelve roles, y adivinar
    quién es docente por el nombre sería justo el tipo de invento que la skill no hace.

    `group_ids`: comisiones del tutor. Los foros del campus son compartidos por TODO el
    curso (27 comisiones en Prog I), así que sin este filtro te llegan los hilos de los
    alumnos de los otros 26 tutores. Las discusiones "para todos" (groupid <= 0) entran
    siempre. Si no se pasa, se revisa el curso entero y se avisa.

    Los foros de avisos se saltean salvo `incluir_avisos`: son de una vía, el tutor
    publica y nadie espera respuesta suya. Se descartan por tipo `news` Y por nombre —
    en este campus "Avisos de la comision" está declarado `general`, pero de hecho es un
    tablón de anuncios: filtrarlo solo por tipo dejaba entrar cientos de avisos de otros
    tutores como si fueran consultas sin responder.
    """
    import asyncio

    fs = await listar_foros(client, course_id)
    if fs.get("error"):
        return fs
    foros, salteados = [], []
    for f in fs["foros"]:
        nombre = f.get("nombre") or ""
        if not incluir_avisos and (f.get("tipo") == "news" or "aviso" in nombre.lower()):
            salteados.append(nombre); continue
        if solo_consultas and (not _RE_CONSULTAS.search(nombre)
                               or _RE_NO_DOCENTE.search(nombre)):
            salteados.append(nombre); continue
        foros.append(f)

    # Solo abrimos los foros que tienen actividad: numdiscussions viene en el listado.
    con_hilos = [f for f in foros if (f.get("discusiones") or 0) > 0]
    if not con_hilos:
        return {"ok": True, "course_id": course_id, "sin_responder": [], "respondio_otro": [],
                "resumen": {"foros_revisados": len(foros), "discusiones": 0},
                "aviso": "Ningún foro del curso tiene discusiones todavía."}

    uid = await client.api.userid()
    sem = asyncio.Semaphore(5)  # el campus es compartido: no lo martillamos

    async def _hilos(f):
        async with sem:
            r = await leer_foro(client, f["forum_id"], limite=max_discusiones)
        if r.get("error"):
            return []
        return [(f, d) for d in r.get("discusiones", [])]

    pares = [x for sub in await asyncio.gather(*[_hilos(f) for f in con_hilos]) for x in sub]
    total_curso = len(pares)
    aviso_filtro = None
    mis_alumnos_ids: set = set()   # vacío = sin filtro por padrón (guardia / sin group_ids)
    if group_ids:
        # ANTES se filtraba por `groupid` de la discusión, y NO filtraba nada: en este
        # campus los foros de consulta son del curso entero y TODAS las discusiones vienen
        # con groupid = -1, que la regla "≤ 0 entra siempre" dejaba pasar. Resultado: 120
        # de 219 hilos se reportaban como propios teniendo 33 alumnos en un curso de 27
        # comisiones, con consultas de alumnos ajenos y hasta hilos abiertos por otros
        # docentes.
        # Lo que SÍ identifica una consulta como propia es QUIÉN la escribió: se cruza el
        # autor contra el padrón de las comisiones del tutor.
        mis_alumnos: set = set()
        fallas: list[str] = []
        for gid in group_ids:
            als = await _alumnos_de_comision(client, course_id, gid)
            if isinstance(als, dict):
                fallas.append(f"grupo {gid}: {als.get('error')}")
                continue
            mis_alumnos.update(u.get("id") for u in als if u.get("id"))
        if not mis_alumnos:
            # Sin padrón no se puede filtrar. Se devuelve todo, pero DICIÉNDOLO: colar
            # hilos ajenos en silencio es lo que hacía la versión anterior.
            aviso_filtro = ("No pude armar el padrón de tus comisiones "
                            f"({'; '.join(fallas) or 'sin alumnos'}), así que NO filtré: "
                            "abajo hay consultas de alumnos de otros tutores.")
        else:
            # OJO: `autor_userid` es de quien ABRIÓ la discusión, no de quien preguntó
            # último. Filtrar sólo por eso escondía consultas de alumnos propios colgadas
            # dentro de un hilo abierto por otro — pasó el 2026-08-04: dos alumnos
            # preguntaron dentro de un hilo ajeno y la tool informó `sin_responder: 0`.
            # Ahora los hilos ajenos CON réplicas no se descartan: se abren igual y se
            # deciden mirando los posts de adentro (ver `_mia`).
            pares = [(f, d) for f, d in pares
                     if d.get("autor_userid") in mis_alumnos or d.get("replicas")]
            mis_alumnos_ids = mis_alumnos
    # Ordenar ANTES de cortar: si no, el tope se come discusiones recientes según el orden
    # en que respondió el campus y quedan pendientes invisibles (pasó: de 351 hilos se
    # clasificaban 120 arbitrarios y los sin responder del resto no aparecían nunca).
    pares.sort(key=lambda p: -(p[1].get("ultimo_ts") or 0))
    truncado = len(pares) > max_discusiones
    pares = pares[:max_discusiones]

    sin_responder, dudosas = [], []
    for f, d in pares:
        fila = {"foro": f["nombre"], "forum_id": f["forum_id"],
                "discussion_id": d["discussion_id"], "titulo": d["titulo"],
                "autor": d["autor"], "autor_userid": d.get("autor_userid"),
                "replicas": d["replicas"],
                "ultimo_ts": d["ultimo_ts"], "puede_responder": d["puede_responder"]}
        (sin_responder if not d["replicas"] else dudosas).append(fila)

    # Las que tienen réplicas: hay que abrirlas para saber si el tutor ya pasó por ahí.
    async def _mia(fila):
        async with sem:
            r = await leer_discusion(client, fila["discussion_id"])
        if r.get("error"):
            fila["aviso"] = "No pude abrirla para ver si respondiste."
            return fila
        posts = r.get("posts", [])
        if not posts:
            return fila
        ult = posts[-1]
        # "Respondida" es que la ÚLTIMA palabra sea tuya, no que hayas hablado alguna vez.
        # Antes bastaba con un post tuyo en cualquier lado para cerrar el hilo para
        # siempre; si después llegaban preguntas nuevas, quedaban invisibles.
        if ult.get("autor_userid") == uid:
            return None
        # Hilo abierto por alguien ajeno: sólo cuenta si adentro preguntó un alumno tuyo.
        if mis_alumnos_ids and fila.get("autor_userid") not in mis_alumnos_ids:
            if not any(p.get("autor_userid") in mis_alumnos_ids for p in posts):
                return None
        fila["ultimo_post_id"] = ult.get("post_id")
        fila["ultimo_autor"] = ult.get("autor")
        fila["responder_a_post"] = ult.get("post_id")
        return fila

    respondio_otro = [x for x in await asyncio.gather(*[_mia(f) for f in dudosas]) if x]

    # El primer post de la discusión es al que hay que responder: lo resolvemos para las
    # que no tienen réplicas, así el tutor puede encadenar directo con responder_foro.
    async def _primer_post(fila):
        async with sem:
            r = await leer_discusion(client, fila["discussion_id"])
        if not r.get("error") and r.get("posts"):
            fila["responder_a_post"] = r["posts"][0]["post_id"]
            fila["texto"] = r["posts"][0]["texto"][:400]
        return fila

    sin_responder = list(await asyncio.gather(*[_primer_post(f) for f in sin_responder]))

    orden = lambda x: -(x.get("ultimo_ts") or 0)  # noqa: E731 — más reciente primero
    out = {"ok": True, "course_id": course_id,
           "sin_responder": sorted(sin_responder, key=orden),
           "respondio_otro": sorted(respondio_otro, key=orden),
           "resumen": {"foros_revisados": len(foros), "foros_con_hilos": len(con_hilos),
                       "discusiones_del_curso": total_curso, "discusiones_tuyas": len(pares),
                       "sin_responder": len(sin_responder),
                       "respondio_otro": len(respondio_otro)},
           "foros_salteados": salteados,
           "_meta": {
               "fuente": "vivo",
               "criterio_filtro": (
                   "Se cuenta como tuya la consulta cuyo AUTOR es alumno de tus comisiones. "
                   "No se filtra por el grupo de la discusión: en este campus los foros de "
                   "consulta son del curso entero y todas vienen con groupid = -1."
                   if group_ids else "sin filtro (no se pasaron group_ids)"),
               "degradado": bool(aviso_filtro),
           }}
    avisos = []
    if aviso_filtro:
        avisos.append(aviso_filtro)
    if not group_ids:
        avisos.append("Sin filtro de comisión: estás viendo los hilos de TODO el curso, "
                      "incluidos los alumnos de otros tutores. Pasá `group_ids` (o mapeá "
                      "tus comisiones con mi_comision / guardar_mis_datos).")
    if truncado:
        avisos.append(f"Corté en {max_discusiones} discusiones: hay más, subí "
                      "max_discusiones si necesitás verlas todas.")
    if avisos:
        out["aviso"] = " ".join(avisos)
    return out


# ---------- MENSAJERÍA (core_message por token) ----------

async def leer_mensajes(client, limite: int = 15) -> list[dict]:
    """Conversaciones recientes por API REST (core_message_get_conversations)."""
    try:
        uid = await client.api.userid()
        data = await client.ws("core_message_get_conversations", {
            "userid": uid, "limitfrom": 0, "limitnum": limite,
            "type": None, "favourites": None, "mergeself": True,
        })
    except MoodleWSError as e:
        return [{"error": f"No pude leer las conversaciones: {e.errorcode}"}]
    out = []
    for conv in (data or {}).get("conversations", []):
        otros = [m for m in conv.get("members", []) if m.get("id") != uid]
        msgs = conv.get("messages", [])
        ultimo = max(msgs, key=lambda m: m.get("timecreated", 0)) if msgs else None
        out.append({
            "conversacion_id": conv.get("id"),
            "alumno": otros[0].get("fullname") if otros else "(yo)",
            "no_leidos": conv.get("unreadcount") or 0,
            "ultimo_mensaje": _plano(ultimo.get("text", ""), 400) if ultimo else "",
            "de_quien": "tutor" if ultimo and ultimo.get("useridfrom") == uid else "alumno",
            "timestamp": ultimo.get("timecreated") if ultimo else None,
        })
    return out


async def leer_conversacion(client, conversacion_id: int, limite: int = 30) -> dict:
    """Mensajes de una conversación, en orden cronológico. `leer_mensajes` solo trae el
    último de cada una: para contestar con contexto hay que abrir el hilo."""
    try:
        uid = await client.api.userid()
        d = await client.ws("core_message_get_conversation_messages", {
            "currentuserid": uid, "convid": conversacion_id,
            "limitfrom": 0, "limitnum": limite, "newest": 1})
    except MoodleWSError as e:
        return {"error": f"No pude abrir la conversación {conversacion_id}: {e.errorcode}"}
    nombres = {m.get("id"): m.get("fullname") for m in (d or {}).get("members", [])}
    msgs = [{"mensaje_id": m.get("id"), "de_quien": "tutor" if m.get("useridfrom") == uid else "alumno",
             "autor": nombres.get(m.get("useridfrom")), "fecha_ts": m.get("timecreated"),
             "texto": _plano(m.get("text", ""))}
            for m in (d or {}).get("messages", [])]
    msgs.sort(key=lambda m: m.get("fecha_ts") or 0)
    otros = [n for i, n in nombres.items() if i != uid]
    return {"ok": True, "conversacion_id": conversacion_id,
            "alumno": otros[0] if otros else None, "total": len(msgs), "mensajes": msgs}


# Marcadores de un anuncio masivo de coordinación reenviado por privado: llegan como
# mensaje de "alumno" y no esperan respuesta de nadie.
_RE_DIFUSION = re.compile(
    r"estimad[oa]s\s+(estudiantes|alumn)|les\s+(recordamos|informamos|escribimos|comunicamos)"
    r"|📢|recorda(mos|torio)\s+que|plazo\s+para\s+la\s+entrega", re.I)

# Señales de que el mensaje PIDE algo. Se evalúan ANTES que la cortesía, así un
# "muchas gracias, pero me quedó una duda: …" sigue contando como consulta por más que
# arranque agradeciendo.
_RE_PEDIDO = re.compile(
    r"[?¿]|consult|\bdud[ao]s?\b|quer[íi]a\s+saber|querr?[íi]a\s+saber|podr[íi]as?\b"
    r"|necesito|me\s+podr|se\s+puede|es\s+posible|c[óo]mo\s+(hago|puedo|es)|cu[áa]ndo\s"
    r"|avisame|av[íi]same|me\s+decís|me\s+dec[ií]s|qu[ée]\s+tengo\s+que", re.I)

# Marcadores de agradecimiento/cierre. Se buscan en CUALQUIER parte del texto y no sólo al
# principio: "Buenas tardes Juan! muchas gracias por todo…" lleva el saludo adelante, y
# exigir que la cortesía arranque el mensaje lo dejaba clasificado como consulta.
# Es seguro buscar en todo el texto porque _RE_PEDIDO ya corrió antes: cualquier mensaje
# que además pida algo salió de acá como "pregunta".
_RE_CORTESIA = re.compile(
    r"\bgracias\b|\bgrac[ií]as\b|\bagradezco\b|\babrazo\b|saludos|\b(ok(ey|ay)?|dale|perfecto"
    r"|genial|buen[ií]simo|entendido|listo|b[áa]rbaro|joya)\b", re.I)
_LARGO_CORTESIA = 320


def clasificar_mensaje_entrante(texto: str) -> str:
    """'pregunta' | 'cortesia' | 'difusion'. Función pura: decide si algo espera respuesta.

    El orden importa: primero se busca si PIDE algo, después si es cortesía. Al revés, un
    "muchas gracias, pero me quedó una duda" se apartaría como agradecimiento y el alumno
    se quedaría sin respuesta.

    El default es 'pregunta' A PROPÓSITO. Perder la consulta de alguien es mucho peor que
    mostrar un "gracias" de más, así que ante la duda entra a la lista. Antes no había
    ninguna clasificación: 18 conversaciones se reportaban como "esperando respuesta"
    cuando las reales eran ~3, y un contador que exagera seis veces se deja de mirar."""
    t = (texto or "").strip()
    if not t:
        return "pregunta"
    if _RE_DIFUSION.search(t):
        return "difusion"
    if _RE_PEDIDO.search(t):
        return "pregunta"
    if len(t) <= _LARGO_CORTESIA and _RE_CORTESIA.search(t):
        return "cortesia"
    return "pregunta"


async def mensajes_pendientes(client, limite: int = 50) -> dict:
    """Conversaciones privadas que esperan respuesta del tutor: el último mensaje lo
    escribió el alumno Y efectivamente pide algo.

    No alcanza con "el último lo escribió el alumno": así entraban los "muchas gracias" y
    los anuncios masivos de coordinación reenviados por privado. Lo descartado NO se
    esconde — viene en `descartadas` con su motivo, para que se pueda auditar el criterio.

    Se separan las que además tienen no leídos, que son las que el tutor ni abrió."""
    convs = await leer_mensajes(client, limite)
    if convs and isinstance(convs[0], dict) and convs[0].get("error"):
        return {"error": convs[0]["error"]}

    del_alumno = [c for c in convs if c.get("de_quien") == "alumno"]
    pendientes, cortesia, difusion = [], [], []
    for c in del_alumno:
        tipo = clasificar_mensaje_entrante(c.get("ultimo_mensaje", ""))
        c = {**c, "tipo": tipo}
        if tipo == "cortesia":
            cortesia.append(c)
        elif tipo == "difusion":
            difusion.append(c)
        else:
            pendientes.append(c)

    orden = lambda c: -(c.get("timestamp") or 0)  # noqa: E731
    resumen = {
        "conversaciones_revisadas": len(convs),
        "esperando_respuesta": len(pendientes),
        "descartadas_cortesia": len(cortesia),
        "descartadas_difusion": len(difusion),
    }
    out = {
        "ok": True,
        "sin_leer": sorted([c for c in pendientes if c["no_leidos"]], key=orden),
        "leidas_sin_responder": sorted([c for c in pendientes if not c["no_leidos"]], key=orden),
        "descartadas": {
            "cortesia": [{"alumno": c["alumno"], "ultimo_mensaje": c["ultimo_mensaje"][:80]}
                         for c in sorted(cortesia, key=orden)],
            "difusion": [{"alumno": c["alumno"], "ultimo_mensaje": c["ultimo_mensaje"][:80]}
                         for c in sorted(difusion, key=orden)],
        },
        "resumen": resumen,
    }
    if cortesia or difusion:
        out["nota_criterio"] = (
            f"De {len(del_alumno)} conversaciones donde habló el alumno último, "
            f"{len(pendientes)} piden algo. Se apartaron {len(cortesia)} de cortesía "
            f"('gracias', 'ok') y {len(difusion)} anuncios masivos — están en "
            "`descartadas` por si querés revisarlas.")
    return out


async def _touserid(client, alumno: str) -> tuple[int | None, str | None]:
    """Resuelve el destinatario: por email (exacto) o buscando en las conversaciones
    del tutor por nombre (parcial). Devuelve (userid, nombre)."""
    if "@" in alumno:
        return await _userid_por_email(client, alumno.strip())
    try:
        uid = await client.api.userid()
        convs = await client.ws("core_message_get_conversations", {
            "userid": uid, "limitfrom": 0, "limitnum": 200,
            "type": None, "favourites": None, "mergeself": True})
    except MoodleWSError:
        return None, None
    buscado = alumno.strip().lower()
    for conv in (convs or {}).get("conversations", []):
        for m in conv.get("members", []):
            if m.get("id") != uid and buscado in (m.get("fullname") or "").lower():
                return m["id"], m.get("fullname")
    return None, None


async def responder_mensaje(client, alumno: str, texto: str, confirmado: bool = False) -> dict:
    """Envía un mensaje privado a un alumno (core_message_send_instant_messages). Resuelve
    el destinatario por email o por nombre entre las conversaciones. Escritura: preview
    primero, y con OK del tutor confirmado=true."""
    if not confirmado:
        return {"requiere_confirmacion": True, "preview": {"alumno": alumno, "texto": texto},
                "aviso": "Confirmá (confirmado=true) para enviar el mensaje."}
    uid, nombre = await _touserid(client, alumno)
    if uid is None:
        return {"error": f"No encontré a '{alumno}' (probá con el email o revisá leer_mensajes)."}
    import time
    try:
        res = await client.ws("core_message_send_instant_messages", {"messages": [
            {"touserid": uid, "text": texto, "textformat": 1,
             "clientmsgid": "copiloto-" + str(int(time.time() * 1000))}]})
    except MoodleWSError as e:
        return {"error": f"No pude enviar el mensaje: {e.errorcode} — {e.message}"}
    enviado = (res or [{}])[0]
    msgid = enviado.get("msgid")
    if enviado.get("errormessage") or (msgid is not None and msgid < 0):
        return {"error": f"Moodle no confirmó el envío: {enviado.get('errormessage') or res}"}
    return {"ok": True, "alumno": nombre or alumno, "touserid": uid, "msgid": msgid}


# ---------- DESCUBRIMIENTO (matrículas + grupos) ----------

async def descubrir_cursos(client) -> list[dict]:
    """Cursos donde el tutor está matriculado (core_enrol_get_users_courses)."""
    try:
        uid = await client.api.userid()
        cursos = await client.ws("core_enrol_get_users_courses", {"userid": uid})
    except MoodleWSError as e:
        return [{"error": f"No pude descubrir cursos: {e.errorcode}"}]
    return [{"course_id": c.get("id"), "nombre": c.get("fullname"), "shortname": c.get("shortname")}
            for c in (cursos or [])]


async def descubrir_comisiones(client, course_id: int) -> list[dict]:
    """Grupos del curso (core_group_get_course_groups). Incluye auxiliares (Grupo_NNN,
    Entrego_*); el caller filtra por el patrón de comisión (ej. 'M26 C1-')."""
    try:
        gs = await client.ws("core_group_get_course_groups", {"courseid": course_id})
    except MoodleWSError as e:
        return [{"error": f"No pude descubrir comisiones: {e.errorcode}"}]
    return [{"group_id": g.get("id"), "nombre": g.get("name")} for g in (gs or [])]


# ---------- ENTREGAS (descarga por token) ----------

# Extensiones que se pueden leer como texto para corregir. El resto (pdf, docx,
# imágenes) se baja igual pero se devuelve la ruta para abrirlo con otra herramienta.
_EXT_TEXTO = {".py", ".txt", ".md", ".csv", ".json", ".ipynb", ".js", ".ts", ".html",
              ".css", ".java", ".c", ".cpp", ".h", ".sql", ".sh", ".r", ".yml", ".yaml"}
_EXT_BINARIO_CONOCIDO = {".pdf", ".docx", ".doc", ".xlsx", ".png", ".jpg", ".jpeg", ".gif"}


def _leer_texto(path: str, max_chars: int) -> tuple[str, bool]:
    """Contenido de un archivo de texto, tolerando encodings raros. (texto, truncado)."""
    import pathlib as _pl

    crudo = _pl.Path(path).read_bytes()
    for enc in ("utf-8", "latin-1"):
        try:
            txt = crudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return "(no pude decodificar el archivo como texto)", False
    return (txt[:max_chars], True) if len(txt) > max_chars else (txt, False)


def _extraer_zip(path: str, destino: str) -> list[str]:
    """Extrae un .zip y devuelve las rutas extraídas.

    Sanea cada nombre contra 'zip slip': un .zip con entradas tipo `../../.ssh/authorized_keys`
    escribiría FUERA del destino. Como acá el zip lo sube un alumno (input no confiable),
    se descartan rutas absolutas y las que se escapen del directorio."""
    import os
    import zipfile

    fuera = []
    destino_abs = os.path.realpath(destino)
    os.makedirs(destino_abs, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for miembro in z.namelist():
            if miembro.endswith("/"):
                continue
            limpio = os.path.normpath(miembro).lstrip("/\\")
            final = os.path.realpath(os.path.join(destino_abs, limpio))
            if not final.startswith(destino_abs + os.sep):
                log.warning("zip: descarto entrada sospechosa %r", miembro)
                continue
            os.makedirs(os.path.dirname(final), exist_ok=True)
            with z.open(miembro) as src, open(final, "wb") as dst:
                dst.write(src.read())
            fuera.append(final)
    return fuera


async def leer_entrega(client, cmid: str, email: str, dest_dir: str,
                       max_chars: int = 20000) -> dict:
    """Baja la entrega de un alumno y devuelve su CONTENIDO, listo para corregir.

    `bajar_entrega` ya existía pero nunca se expuso como tool, así que la skill podía
    escribir una nota sin que nadie hubiera visto el trabajo. Esto cierra ese agujero:
    descomprime los .zip (la forma en que se entrega en la TUP), lee los archivos de
    código/texto y deja la ruta local de los binarios (PDF, imágenes) para abrirlos aparte.
    """
    import os

    bajado = await bajar_entrega(client, cmid, email, dest_dir)
    if bajado.get("error"):
        return bajado

    archivos: list[dict] = []
    rutas = [a["archivo"] for a in bajado.get("archivos", [])]
    for ruta in list(rutas):
        if os.path.splitext(ruta)[1].lower() == ".zip":
            try:
                extra = _extraer_zip(ruta, ruta + "_extraido")
                rutas.extend(extra)
            except Exception as e:  # noqa: BLE001
                archivos.append({"archivo": os.path.basename(ruta),
                                 "error": f"No pude abrir el zip: {type(e).__name__}: {e}"})

    presupuesto = max_chars
    for ruta in rutas:
        ext = os.path.splitext(ruta)[1].lower()
        if ext == ".zip":
            continue
        item = {"archivo": os.path.basename(ruta), "ruta": ruta,
                "bytes": os.path.getsize(ruta) if os.path.exists(ruta) else 0}
        if ext in _EXT_TEXTO and presupuesto > 0:
            txt, truncado = _leer_texto(ruta, presupuesto)
            presupuesto -= len(txt)
            item["contenido"] = txt
            if truncado:
                item["truncado"] = True
        elif ext in _EXT_BINARIO_CONOCIDO:
            item["nota"] = "Binario: abrilo desde `ruta` con la herramienta de lectura."
        else:
            item["nota"] = "Extensión no reconocida como texto; está en `ruta`."
        archivos.append(item)

    legibles = sum(1 for a in archivos if "contenido" in a)
    return {
        "ok": True,
        "assign_id": str(cmid),
        "email": email,
        "archivos": archivos,
        "resumen": f"{len(archivos)} archivo(s), {legibles} legible(s) como texto.",
        "_meta": {"fuente": "vivo", "truncado_a_chars": max_chars},
    }


async def bajar_entrega(client, cmid: str, email: str, dest_dir: str) -> dict:
    """Descarga el/los archivo(s) entregados por un alumno (mod_assign_get_submissions +
    descarga con token). Devuelve el path del archivo principal."""
    import os

    inst = await _instanceid(client, cmid)
    if inst is None:
        return {"error": f"No encontré la tarea cmid={cmid}."}
    uid, _ = await _userid_por_email(client, email)
    if uid is None:
        return {"error": f"No encontré al alumno {email}."}
    try:
        subs = await client.ws("mod_assign_get_submissions", {"assignmentids": [inst]})
    except MoodleWSError as e:
        return {"error": f"get_submissions falló: {e.errorcode}"}
    files = []
    for asg in (subs or {}).get("assignments", []):
        for s in asg.get("submissions", []):
            if int(s.get("userid", -1)) != uid:
                continue
            for pl in s.get("plugins", []):
                for fa in pl.get("fileareas", []):
                    for f in fa.get("files", []):
                        if f.get("fileurl"):
                            files.append((f.get("filename") or "entrega", f["fileurl"]))
    if not files:
        return {"error": f"No encontré archivo entregado de {email} en la tarea {cmid}."}
    os.makedirs(dest_dir, exist_ok=True)
    guardados = []
    for fname, url in files:
        data = await client.token_download(url)
        path = os.path.join(dest_dir, f"{uid}_{fname}")
        with open(path, "wb") as fh:
            fh.write(data)
        guardados.append({"archivo": path, "bytes": len(data)})
    return {"ok": True, "archivos": guardados, "principal": guardados[0]["archivo"]}
