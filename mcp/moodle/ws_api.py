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
# La escala 5 (TPs de Prog I) se confirmó leyendo el <select> del grader real:
# value 1 = "Aprobado", value 2 = "Desaprobado" (NO es guessable, van invertidos).
_ESCALAS: dict[int, dict[str, int]] = {
    5: {"aprobado": 1, "desaprobado": 2},
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

async def _assign_map(client) -> dict:
    """{cmid(str): {"id": instanceid, "course": courseid, "grade": gradeval, "name": str}}.
    `grade` < 0 → escala (-scaleid); ≥ 0 → nota numérica (puntos máx)."""
    cache = getattr(client, "_assign_map_cache", None)
    if cache is not None:
        return cache
    data = await client.ws("mod_assign_get_assignments")
    m: dict = {}
    for c in (data or {}).get("courses", []):
        for a in c.get("assignments", []):
            m[str(a.get("cmid"))] = {
                "id": a.get("id"),
                "course": a.get("course"),
                "grade": a.get("grade"),
                "name": a.get("name", ""),
            }
    client._assign_map_cache = m
    return m


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


async def pendientes_tarea(client, cmid: str, group_id: int = 0) -> dict:
    """Alumnos que entregaron y siguen sin nota (requiregrading), por API."""
    ps = await _participantes(client, cmid, group_id)
    if isinstance(ps, dict):
        return ps
    alumnos = [
        {"name": p.get("fullname"), "email": p.get("email"), "userid": p.get("id"),
         "grupo": next((g.get("name") for g in p.get("groups", [])), None)}
        for p in ps if _es_estudiante(p) and p.get("submitted") and p.get("requiregrading")
    ]
    return {"assign_id": cmid, "group_id": group_id, "pendientes": len(alumnos), "alumnos": alumnos}


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
        else:
            estado = "Calificado"
        alumnos.append({
            "nombre": p.get("fullname"),
            "email": (p.get("email") or "").lower(),
            "userid": p.get("id"),
            "estado": estado,
            "nota": nota,
            "entregado": entregado,
            "pendiente": entregado and requiere,
        })

    # Los que deben primero, después la cola de corrección, y al final los cerrados.
    _orden = {"Sin entrega": 0, "Enviado para calificar": 1, "Calificado": 2}
    alumnos.sort(key=lambda a: (_orden[a["estado"]], _norm(a["nombre"] or "")))

    return {
        "assign_id": str(cmid),
        "group_id": group_id,
        "tarea": cfg.get("name", ""),
        "participantes": len(est),
        "entregados": sum(1 for a in alumnos if a["entregado"]),
        "pendientes_por_corregir": sum(1 for a in alumnos if a["pendiente"]),
        "sin_entrega": sum(1 for a in alumnos if not a["entregado"]),
        "alumnos": alumnos,
    }


# ---------- ESCRITURA de nota (mod_assign_save_grade) ----------

def _resolver_valor_nota(grade_cfg, nota) -> tuple[float | None, str | None, list | None]:
    """Traduce la nota pedida al valor que espera save_grade.
    - grade_cfg < 0 → ESCALA (-scaleid): 'Aprobado'/'Desaprobado' -> índice (1/2).
    - grade_cfg ≥ 0 → NUMÉRICA: se usa el número tal cual (coma o punto).
    Devuelve (valor, etiqueta, opciones_validas | None). valor None = no resuelto."""
    try:
        gc = int(grade_cfg)
    except (TypeError, ValueError):
        gc = 0
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
    escala → 'Aprobado'/'Desaprobado'; numérica → el número limpio. None si no hay nota."""
    if grade_value in (None, "", "-1.00000", "-1"):
        return None
    try:
        gc = int(grade_cfg)
    except (TypeError, ValueError):
        gc = 0
    if gc < 0:
        mapa = _ESCALAS.get(-gc)
        if mapa:
            try:
                idx = int(float(str(grade_value).replace(",", ".")))
            except (TypeError, ValueError):
                return str(grade_value)
            for k, v in mapa.items():
                if v == idx:
                    return k.capitalize()
        return str(grade_value)
    try:
        f = float(str(grade_value).replace(",", "."))
        return str(int(f)) if f == int(f) else f"{f:g}"
    except (TypeError, ValueError):
        return str(grade_value)


async def cargar_nota(client, cmid: str, email: str, nota, mensaje: str, confirmado: bool = False) -> dict:
    """Escribe nota + devolución por API REST (mod_assign_save_grade). Sin navegador.
    Escala (TPs): pasar 'Aprobado'/'Desaprobado'. Numérica (TIO): el número. Verifica
    con get_grades que la nota quedó guardada."""
    amap = await _assign_map(client)
    cfg = amap.get(str(cmid))
    if cfg is None:
        return {"error": f"No encontré la tarea cmid={cmid} en tus cursos."}
    valor, etiqueta, opciones = _resolver_valor_nota(cfg.get("grade"), nota)
    if valor is None:
        return {"error": f"'{nota}' no es una nota válida para esta tarea.",
                "es_escala": cfg.get("grade", 0) < 0, "opciones": opciones}
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
    try:
        await client.ws("mod_assign_save_grade", {
            "assignmentid": inst, "userid": uid, "grade": valor, "attemptnumber": -1,
            "addattempt": 0, "workflowstate": "", "applytoall": 1,
            "plugindata": {"assignfeedbackcomments_editor": {"text": html_msg, "format": 1}},
        })
    except MoodleWSError as e:
        return {"error": f"No pude guardar la nota: {e.errorcode} — {e.message}"}

    # Verificación: releer la nota por API.
    verificado = False
    try:
        g = await client.ws("mod_assign_get_grades", {"assignmentids": [inst]})
        for asg in (g or {}).get("assignments", []):
            for gr in asg.get("grades", []):
                if int(gr.get("userid")) == uid:
                    verificado = abs(float(gr.get("grade")) - valor) < 0.001
    except MoodleWSError:
        pass
    return {"ok": True, "alumno": nombre or email, "nota": etiqueta, "verificado": verificado}


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
             "autor": x.get("userfullname"), "replicas": x.get("numreplies", 0),
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
    if group_ids:
        gs = set(group_ids)
        # groupid <= 0 = discusión visible para todo el curso: siempre entra.
        pares = [(f, d) for f, d in pares if (d.get("group_id") or 0) <= 0
                 or d.get("group_id") in gs]
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
                "autor": d["autor"], "replicas": d["replicas"],
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
        if any(p.get("autor_userid") == uid for p in posts):
            return None  # ya respondiste
        ult = posts[-1] if posts else {}
        fila["ultimo_post_id"] = ult.get("post_id")
        fila["ultimo_autor"] = ult.get("autor")
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
           "foros_salteados": salteados}
    if not group_ids:
        out["aviso"] = ("Sin filtro de comisión: estás viendo los hilos de TODO el curso, "
                        "incluidos los alumnos de otros tutores. Pasá `group_ids` (o mapeá "
                        "tus comisiones con mi_comision / guardar_mis_datos).")
    if truncado:
        out["aviso"] = (out.get("aviso", "") + f" Corté en {max_discusiones} discusiones: "
                        "hay más, subí max_discusiones si necesitás verlas todas.").strip()
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


async def mensajes_pendientes(client, limite: int = 50) -> dict:
    """Conversaciones privadas que esperan respuesta del tutor: el último mensaje lo
    escribió el alumno. Es el "qué me falta contestar" de la mensajería.

    Se separan las que además tienen no leídos, que son las que el tutor ni abrió."""
    convs = await leer_mensajes(client, limite)
    if convs and isinstance(convs[0], dict) and convs[0].get("error"):
        return {"error": convs[0]["error"]}
    pendientes = [c for c in convs if c.get("de_quien") == "alumno"]
    orden = lambda c: -(c.get("timestamp") or 0)  # noqa: E731
    return {"ok": True,
            "sin_leer": sorted([c for c in pendientes if c["no_leidos"]], key=orden),
            "leidas_sin_responder": sorted([c for c in pendientes if not c["no_leidos"]], key=orden),
            "resumen": {"conversaciones_revisadas": len(convs),
                        "esperando_respuesta": len(pendientes)}}


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
