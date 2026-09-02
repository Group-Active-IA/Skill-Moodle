"""El LIBRO DE CALIFICACIONES: qué hizo cada alumno y con qué nota.

Existe porque en Matemática toda la skill estaba mirando el módulo equivocado.

**El hallazgo.** `mod_assign` —la única fuente de "entregas" que la skill conocía— está
en CERO ABSOLUTO en Matemática: verificado en vivo el 2026-09-02, 549 participantes y
0 enviados en las 15 actividades del curso, comisión por comisión. No es que la materia
esté atrasada: la cursada **no pasa por ahí**. Lo que el alumno hace son videos
interactivos H5P, lecciones y autoevaluaciones, y todo eso vive en el calificador.
Medido en la comisión 01 (40 alumnos):

    hvp    (videos)          29 items    240 notas cargadas
    lesson (lecciones)       14 items     89
    quiz   (autoevaluaciones)23 items     99
    assign (entregas)        15 items      0   <-- lo ÚNICO que la skill leía

34 de esos 40 alumnos tienen actividad. Con la vista vieja los 40 salían en blanco, y
un padrón entero en cero se lee como "esta comisión no arrancó" cuando en realidad
arrancó y nadie lo estaba viendo. Ése es el peor error de este proyecto: el dato falso
que se lee perfecto.

**De dónde sale la UNIDAD, y por qué no del título.** Los títulos del calificador no
declaran unidad: dicen "Video 2 Semana 1 SN", "Logica Lección Semana 2". El tema está
abreviado (AB, SN, Log) y un mapa tema->unidad escrito a mano es exactamente el reparto
que vence sin avisar. La unidad la nombra la ESTRUCTURA del curso, que sí es explícita:

    [sec 9]  '2- Sistema binario'      <- encabezado numerado: abre la unidad 2
    [sec 11] 'Videos'                  <- cuelga de la 2
    [sec 12] 'Lecciones'               <- cuelga de la 2
    [sec 14] 'Trabajo Práctico'        <- cuelga de la 2
    [sec 15] 'Autoevaluaciones'        <- cuelga de la 2
    [sec 16] '3- Lógica'               <- encabezado numerado: abre la unidad 3

Y se verifica sola: dentro de cada bloque cae la tarea `ENTREGA U{n}S{m}`, que SÍ dice
su unidad en el título. Las 13 de Matemática caen 13 de 13 en la unidad que la sección
les asigna. Cuando no coincidan, **gana el título** (es explícito) y el desacuerdo se
declara — misma precedencia que ya fija `titulos.py`.

Una sección que no es encabezado numerado y tampoco es uno de los sub-bloques conocidos
NO hereda nada: abre territorio propio y sus actividades quedan `unidad=None`. Eso es lo
que mantiene afuera a 'COLOQUIOS' (7 cuestionarios de repaso que están DESPUÉS de la
unidad 6 y no son de la unidad 6), a los dos Trabajos Integradores y al video de
bienvenida. Heredar por posición los habría metido en la unidad de al lado con un número
plausible y equivocado.

**Nada de esto reemplaza a `mod_assign`.** Programación sí entrega por ahí y esa vista
sigue siendo la buena para esa materia. Éste es el otro ojo, y las dos cosas conviven:
el catálogo dice de qué tipo es cada actividad y quien lee decide qué mirar.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata

from . import titulos
from .cliente import MoodleWSError

# Cuántas comisiones se piden a la vez. Mismo techo que el resto del módulo de informes:
# el campus es compartido con 25 tutores y ~550 alumnos y no se lo martilla.
_SEM = 4

# Qué módulo de Moodle es qué cosa para el que lee el informe. La clave es `itemmodule`,
# que viene en cada item del calificador y es dato del campus, no del título.
TIPOS = {
    "hvp": "video",            # H5P interactivo — lo que la cátedra llama "los videos"
    "lesson": "leccion",
    "quiz": "autoevaluacion",
    "assign": "entrega",
}
# El orden en que se muestran. No es alfabético: es el de la cursada — mirás el video,
# hacés la lección, te autoevaluás, entregás.
ORDEN_TIPOS = ("video", "leccion", "autoevaluacion", "entrega")

# Un encabezado de unidad: "1 - Algebra de Boole", "2- Sistema binario", "6 - Arboles y
# Grafos". El número al principio seguido de guion es la firma; el nombre del tema no se
# usa para nada, justamente para no depender de cómo lo escribieron.
_RE_SECCION_UNIDAD = re.compile(r"^(\d{1,2})\s*[-–—.]\s*\S")

# Las secciones que CUELGAN del encabezado de unidad. Está escrita la lista y no una
# heurística porque el costo de los dos errores no es el mismo: no reconocer un
# sub-bloque deja actividades sin unidad (visible, se declara), mientras que heredar de
# más le adjudica a la unidad 6 los siete cuestionarios de la sección COLOQUIOS, que es
# un número plausible y equivocado.
_SUBBLOQUES = frozenset({
    "videos", "video", "lecciones", "leccion", "material de lectura",
    "material de lecturas", "actividades ludicas", "trabajo practico",
    "trabajos practicos", "trabajo practicos", "autoevaluaciones", "autoevaluacion",
})

# Moodle mete HTML en `gradeformatted`: el ícono de aprobado viene como un <i class=...>
# pegado al número. Renderizarlo tal cual pone '<i class="afaicon fa fa-check...">10,00'
# en una celda del PDF. Se saca el marcado y queda el número.
_RE_TAGS = re.compile(r"<[^>]+>")


def _norm(txt: str) -> str:
    """Minúsculas, sin acentos y con los espacios colapsados. Mismo criterio que
    `titulos._norm`."""
    s = unicodedata.normalize("NFKD", str(txt or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(s.split())


def _num(x) -> float | None:
    """Número o None. `None` = no hay nota, que NO es lo mismo que un cero."""
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def limpiar_nota(txt: str) -> str:
    """`gradeformatted` sin el HTML que Moodle le mete adentro. PURA.

    Verificado en vivo: la nota llega como
    `'<i class="afaicon fa fa-check text-success inline fa-fw" title="Aprobado" ...></i>10,00'`.
    El `-` de "sin nota" llega limpio, así que sin este filtro las celdas con nota se ven
    peor que las vacías.
    """
    return " ".join(_RE_TAGS.sub("", str(txt or "")).split())


# ---------------------------------------------------------------------------
# Catálogo de actividades: qué es cada cosa y de qué unidad. PURO.
# ---------------------------------------------------------------------------

def catalogo_de_actividades(contenidos: list[dict]) -> dict:
    """Lee la estructura del curso y devuelve qué actividad calificable es cada cosa.

    PURA: recibe lo que devuelve `core_course_get_contents` y no consulta nada.

    -> {"items": [ ... ], "por_cmid": {cmid: item}, "unidades": [1, 2, ...],
        "avisos": [...]}

    Cada item: `cmid`, `titulo`, `modname`, `tipo`, `unidad`, `semana`, `seccion`,
    `orden`. `unidad`/`semana` en None significan **no se pudo leer**, nunca cero.
    """
    items: list[dict] = []
    avisos: list[str] = []
    sin_unidad: list[str] = []
    unidad_actual: int | None = None
    huerfanas_de_seccion: list[str] = []

    for sec in contenidos or []:
        nombre = sec.get("name") or ""
        n = _norm(nombre)
        m = _RE_SECCION_UNIDAD.match(n)
        if m:
            unidad_actual = int(m.group(1))
        elif n not in _SUBBLOQUES:
            # Sección con nombre propio que no es un sub-bloque de unidad: corta la
            # herencia. Es lo que deja afuera a COLOQUIOS y a los integradores.
            if unidad_actual is not None:
                huerfanas_de_seccion.append(nombre)
            unidad_actual = None

        for i, mod in enumerate(sec.get("modules") or []):
            tipo = TIPOS.get(mod.get("modname"))
            if tipo is None:
                continue  # etiquetas, foros, urls: no se califican
            cmid = mod.get("id")
            if cmid is None:
                continue
            titulo = mod.get("name") or f"cmid {cmid}"
            leido = titulos.leer(titulo)
            unidad = unidad_actual
            # Precedencia: el título gana cuando declara la unidad de forma explícita
            # (`U{n}S{m}`). Es la misma regla de `titulos.py` y acá además sirve de
            # verificación cruzada de la lectura por sección.
            if leido.unidad is not None:
                if unidad is not None and unidad != leido.unidad:
                    avisos.append(
                        f"«{titulo}» está en la sección de la unidad {unidad} pero su "
                        f"título declara la unidad {leido.unidad}. Gana el título "
                        "(es explícito); revisá dónde está puesta la actividad.")
                unidad = leido.unidad
            if unidad is None:
                sin_unidad.append(titulo)
            items.append({
                "cmid": int(cmid),
                "titulo": titulo,
                "modname": mod.get("modname"),
                "tipo": tipo,
                "unidad": unidad,
                "semana": leido.semana,
                "seccion": nombre,
                "orden": i,
                "fuera_de_cadencia": leido.fuera_de_cadencia,
            })

    # Orden de lectura: por unidad, dentro de la unidad por semana, y dentro de la
    # semana por el orden en que están puestas en el aula. Lo que no declara unidad va
    # al final y no se mezcla con lo que sí. Ordenar por título dejaba el "Video 4
    # Semana 1" después del "Video 1 Semana 2".
    items.sort(key=lambda it: (
        it["unidad"] is None, it["unidad"] or 0,
        ORDEN_TIPOS.index(it["tipo"]) if it["tipo"] in ORDEN_TIPOS else 9,
        it["semana"] is None, it["semana"] or 0, it["orden"], it["titulo"],
    ))
    for pos, it in enumerate(items):
        it["nro"] = pos + 1  # número de columna en la matriz del PDF

    unidades = sorted({it["unidad"] for it in items if it["unidad"] is not None})
    if sin_unidad:
        avisos.append(
            f"{len(sin_unidad)} actividad(es) calificables no quedaron bajo ninguna "
            "unidad porque su sección no es un sub-bloque de unidad (integradores, "
            "coloquios, bienvenida). Se listan igual, agrupadas aparte: "
            + ", ".join(sin_unidad[:6]) + ("…" if len(sin_unidad) > 6 else ""))
    if huerfanas_de_seccion:
        avisos.append(
            "Estas secciones cortaron la herencia de unidad por tener nombre propio: "
            + ", ".join(sorted(set(huerfanas_de_seccion))[:8])
            + ". Si alguna era un sub-bloque de unidad escrito distinto, sus "
              "actividades quedaron sin unidad (no mal asignadas).")

    return {"items": items, "por_cmid": {it["cmid"]: it for it in items},
            "unidades": unidades, "avisos": avisos}


# ---------------------------------------------------------------------------
# Cruce alumno x actividad. PURO.
# ---------------------------------------------------------------------------

def filas_de_alumnos(usergrades: list[dict], catalogo: dict,
                     accesos: dict | None = None) -> list[dict]:
    """Una fila por alumno con sus notas ya clasificadas. PURA.

    `usergrades` es lo que devuelve `gradereport_user_get_grade_items`; `accesos`, el
    `{userid: {...}}` del padrón (los dos relojes). Si un alumno no está en `accesos`
    la fila lo dice con `sin_dato`, no con un cero: "nunca abrió la materia" y "no pude
    leer cuándo la abrió" son personas distintas y sólo a una hay que escribirle.
    """
    por_cmid = catalogo.get("por_cmid") or {}
    accesos = accesos or {}
    filas = []

    for u in usergrades or []:
        uid = u.get("userid")
        if uid is None:
            continue
        uid = int(uid)
        notas: dict[int, dict] = {}      # cmid -> nota
        por_tipo: dict[str, dict] = {t: {"hechas": 0, "total": 0, "suma_pct": 0.0}
                                     for t in ORDEN_TIPOS}
        por_unidad: dict[int, dict] = {}

        for it in u.get("gradeitems") or []:
            cmid = it.get("cmid")
            meta = por_cmid.get(int(cmid)) if cmid is not None else None
            if meta is None:
                # `itemtype == 'course'` (el total del curso) y cualquier item que no
                # sea un módulo del aula caen acá. No son actividades del alumno.
                continue
            tipo = meta["tipo"]
            crudo = _num(it.get("graderaw"))
            sobre = _num(it.get("grademax"))
            por_tipo[tipo]["total"] += 1
            uni = meta["unidad"]
            if uni is not None:
                b = por_unidad.setdefault(uni, {t: {"hechas": 0, "total": 0}
                                                for t in ORDEN_TIPOS})
                b[tipo]["total"] += 1
            if crudo is None:
                continue  # no la hizo: la celda queda VACÍA, no en cero
            pct = (crudo / sobre * 100) if sobre else None
            por_tipo[tipo]["hechas"] += 1
            if pct is not None:
                por_tipo[tipo]["suma_pct"] += pct
            if uni is not None:
                por_unidad[uni][tipo]["hechas"] += 1
            notas[int(cmid)] = {
                "cmid": int(cmid), "nro": meta["nro"], "titulo": meta["titulo"],
                "tipo": tipo, "unidad": uni, "semana": meta["semana"],
                "nota": round(crudo, 2), "sobre": sobre,
                "porcentaje": round(pct, 1) if pct is not None else None,
                "texto": limpiar_nota(it.get("gradeformatted")),
                "calificado_ts": int(it.get("gradedategraded") or 0),
            }

        for t, b in por_tipo.items():
            b["promedio_pct"] = (round(b["suma_pct"] / b["hechas"], 1)
                                 if b["hechas"] else None)
            b.pop("suma_pct")

        hechas = sum(b["hechas"] for b in por_tipo.values())
        acc = accesos.get(uid) or {}
        filas.append({
            "userid": uid,
            "nombre": u.get("userfullname") or "",
            "notas": notas,
            "por_tipo": por_tipo,
            "por_unidad": por_unidad,
            "actividades_con_nota": hechas,
            "sin_actividad": hechas == 0,
            # Los dos relojes, crudos y en días. El de LA MATERIA es el que importa;
            # el del campus está al lado porque los dos juntos separan "abandonó" de
            # "entra todos los días y no abre esta materia".
            **_acceso(acc),
        })

    filas.sort(key=lambda f: f["nombre"].upper())
    return filas


def _acceso(u: dict) -> dict:
    """Los dos relojes de un alumno, listos para mostrar. PURA.

    `estado_aula` distingue las tres situaciones que un número solo confunde:
    `abrio` (hay fecha), `nunca_abrio` (el campo vino en 0) y `sin_dato` (el campo NO
    vino: permisos o respuesta incompleta, y ahí decir "nunca abrió" sería inventar).
    """
    if "lastcourseaccess" not in u:
        return {"estado_aula": "sin_dato", "dias_sin_abrir_la_materia": None,
                "ultimo_acceso_aula_ts": None, "ultimo_acceso_campus_ts": None}
    try:
        ts = int(u.get("lastcourseaccess") or 0)
    except (TypeError, ValueError):
        ts = 0
    try:
        ts_campus = int(u.get("lastaccess") or 0)
    except (TypeError, ValueError):
        ts_campus = 0
    if ts <= 0:
        return {"estado_aula": "nunca_abrio", "dias_sin_abrir_la_materia": None,
                "ultimo_acceso_aula_ts": 0, "ultimo_acceso_campus_ts": ts_campus}
    return {"estado_aula": "abrio",
            "dias_sin_abrir_la_materia": int((time.time() - ts) // 86400),
            "ultimo_acceso_aula_ts": ts, "ultimo_acceso_campus_ts": ts_campus}


def resumen_de_comision(filas: list[dict], catalogo: dict) -> dict:
    """Los cuatro números del encabezado del informe. PURO.

    No hay porcentaje de avance y es deliberado — la misma decisión, con la misma
    evidencia, que hizo que `avance_alumnos` no lo tuviera: un porcentaje necesita saber
    cuántas actividades ya deberían estar hechas y ese dato no existe (ninguna actividad
    de Matemática tiene fecha de entrega). Se cuenta lo que pasó, no se estima lo que
    faltaría.
    """
    total_por_tipo = {t: 0 for t in ORDEN_TIPOS}
    for it in catalogo.get("items", []):
        total_por_tipo[it["tipo"]] += 1
    hechas_por_tipo = {t: sum(f["por_tipo"][t]["hechas"] for f in filas)
                       for t in ORDEN_TIPOS}
    con_actividad = sum(1 for f in filas if not f["sin_actividad"])
    return {
        "alumnos": len(filas),
        "con_actividad": con_actividad,
        "sin_actividad": len(filas) - con_actividad,
        "actividades_del_curso": total_por_tipo,
        "notas_cargadas": hechas_por_tipo,
        "sin_dato_de_acceso": sum(1 for f in filas if f["estado_aula"] == "sin_dato"),
        "nunca_abrieron_la_materia": sum(1 for f in filas
                                         if f["estado_aula"] == "nunca_abrio"),
    }


# ---------------------------------------------------------------------------
# Consultas al campus.
# ---------------------------------------------------------------------------

async def contenidos(client, course_id: int) -> tuple[list[dict], str | None]:
    """Estructura del curso (secciones + módulos). Una consulta."""
    try:
        r = await client.ws("core_course_get_contents", {"courseid": course_id})
    except MoodleWSError as e:
        return [], f"no pude leer la estructura del curso: {e.errorcode}"
    if not isinstance(r, list):
        return [], "la estructura del curso no vino como lista"
    return r, None


async def notas_de_comision(client, course_id: int, group_id: int) -> dict:
    """El calificador de UNA comisión, en UNA consulta.

    `gradereport_user_get_grade_items` acepta `groupid` y devuelve todos los items de
    todos los alumnos del grupo de una sola vez: 15 consultas para la materia entera, no
    15 x 81 actividades. Es la razón por la que este informe es barato.
    """
    try:
        r = await client.ws("gradereport_user_get_grade_items",
                            {"courseid": course_id, "groupid": group_id})
    except MoodleWSError as e:
        # `nopermissiontoviewgrades` / `accessexception` es el caso real de un tutor con
        # rol `teacher` sin permiso sobre el libro completo. Se devuelve como error
        # legible, no como una comisión vacía.
        return {"error": f"no pude leer el calificador del grupo {group_id}: "
                         f"{e.errorcode}"}
    return {"usergrades": (r or {}).get("usergrades") or []}


async def informe(client, course_id: int, comisiones: list[dict],
                  padrones: dict) -> dict:
    """Arma el informe de todas las comisiones pedidas.

    `comisiones`: [{comision, group_id, nombre, tutor}] — el tutor ya resuelto por
    `panorama.elegir_tutor`, que es quien sabe distinguir al tutor de la comisión del
    profesor del curso. `padrones`: {group_id: padrón} de `panorama._padrones`.
    """
    secciones, err = await contenidos(client, course_id)
    if err:
        return {"error": err}
    cat = catalogo_de_actividades(secciones)
    if not cat["items"]:
        return {"error": "el curso no tiene ninguna actividad calificable "
                         "(videos, lecciones, autoevaluaciones ni entregas)."}

    sem = asyncio.Semaphore(_SEM)

    async def _una(c):
        async with sem:
            return c, await notas_de_comision(client, course_id, c["group_id"])

    bloques, avisos = [], list(cat["avisos"])
    for res in await asyncio.gather(*(_una(c) for c in comisiones),
                                    return_exceptions=True):
        if isinstance(res, BaseException):
            avisos.append(f"Una comisión no se pudo leer: {type(res).__name__}: {res}")
            continue
        c, notas = res
        if notas.get("error"):
            avisos.append(f"{c['comision']}: {notas['error']}")
            bloques.append({**c, "error": notas["error"], "alumnos": []})
            continue
        accesos = (padrones.get(c["group_id"], {}) or {}).get("accesos") or {}
        filas = filas_de_alumnos(notas["usergrades"], cat, accesos)
        bloques.append({**c, "alumnos": filas,
                        "resumen": resumen_de_comision(filas, cat)})

    return {"ok": True, "course_id": course_id, "catalogo": cat,
            "comisiones": bloques, "avisos": avisos}
