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
    "quiz": "cuestionario",
    "assign": "entrega",
    # Los dos que trae Probabilidad y Estadística y no existían en las otras materias.
    # El foro CALIFICADO es una actividad del alumno con nota, no un canal de consulta:
    # dejarlo afuera perdía 22 notas por comisión.
    "forum": "foro",
    "glossary": "glosario",
}
# El orden en que se muestran. No es alfabético: es el de la cursada — mirás el video o la
# lección, resolvés el cuestionario, participás del foro, entregás.
ORDEN_TIPOS = ("video", "leccion", "cuestionario", "foro", "glosario", "entrega")

# QUÉ ES cada actividad para el que lee el informe, más allá de con qué módulo está hecha.
# Sin esto el informe de Probabilidad y Estadística mezcla en una misma lista los 36
# cuestionarios semanales, los dos parciales, el coloquio final y TRES columnas donde el
# docente vuelca notas y ningún alumno entrega nunca. Son cuatro cosas que se miran en
# momentos distintos y con criterios distintos.
CADENCIA = "cadencia"            # lo semanal: es contra esto que se mide venir al día
INTEGRADOR = "integrador"        # el TAI/TPI: se entrega por partes a lo largo del cuatrimestre
EVALUACION = "evaluacion"        # parciales, recuperatorios, coloquio: calendario propio
ADMINISTRATIVA = "administrativa"  # columnas de nota, no actividades del alumno

_RE_INTEGRADOR = re.compile(r"ta\s*i|tpi|integrador", re.I)
_RE_EVALUACION = re.compile(r"parcial|recuperatorio|coloquio|examen|extraordinari", re.I)
# La sección lo declara mejor que el título: "CONDICIONES FINALES", "Promedio Trabajo
# Práctico Integrador" y "Calificación Coloquio" viven las tres en la sección "Condiciones
# Finales" y ninguna es una entrega. Por el título solo, "Calificación Coloquio" se leería
# como el coloquio mismo.
_RE_SECCION_ADMIN = re.compile(r"condiciones\s+finales|gesti[oó]n\s+acad", re.I)

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

# El foro de consultas de cada unidad es lo ÚNICO del aula de Probabilidad y Estadística
# que nombra la unidad: "Espacio para Consultas y Dudas sobre la UNIDAD N° 1", "Foro de
# consultas académicas – Unidad 4". Los cuatro abren su bloque, así que sirven de mojón.
# Se exige que el título hable de consultas: "8 - Repaso Unidades N° 1 y 2" menciona DOS
# unidades y no abre ninguna — es el cierre de las dos.
_RE_UNIDAD_MENCION = re.compile(r"unidad(?:es)?\s*n?\s*[°º]?\s*(\d{1,2})", re.I)
_RE_ES_CONSULTA = re.compile(r"consulta|duda", re.I)
# Semanas que no son de ninguna unidad: el repaso cierra varias y el parcial las evalúa.
# Adjudicarles la última unidad abierta pondría el Primer Parcial dentro de la unidad 2.
_RE_SEMANA_SIN_UNIDAD = re.compile(r"repaso|parcial", re.I)


def eje_de_secciones(contenidos: list[dict]) -> tuple[str, int]:
    """¿El número de una sección numerada es una UNIDAD o una SEMANA? PURA.

    -> `("unidad" | "semana", coincidencias)`.

    Es la pregunta que decide el informe entero, y **no se configura por materia: se
    deriva**. Matemática numera 6 secciones y son sus 6 unidades; Probabilidad y
    Estadística numera 16 y son 16 SEMANAS agrupadas en 4 unidades. Aplicarle a la segunda
    el criterio de la primera devuelve "unidad 16" en una materia de cuatro — un número
    plausible y falso, que es la única clase de error que importa acá.

    La señal: las actividades de PyE declaran su semana en un prefijo anclado (`S5-…`). Si
    ese número coincide con el de la sección numerada que las contiene, entonces las
    secciones son semanas. Medido en vivo el 2026-09-02: 41 coincidencias sobre 42
    actividades con prefijo (la única que no coincide está mal puesta en el aula), contra
    CERO en Matemática, que no usa el prefijo en ningún título.
    """
    coincidencias = 0
    actual: int | None = None
    for sec in contenidos or []:
        m = _RE_SECCION_UNIDAD.match(_norm(sec.get("name") or ""))
        if m:
            actual = int(m.group(1))
        for mod in (sec.get("modules") or []):
            if TIPOS.get(mod.get("modname")) is None:
                continue
            sem = titulos.semana_prefijo(mod.get("name") or "")
            if sem is not None and sem == actual:
                coincidencias += 1
    # Tres es el piso para no dar vuelta el criterio por una coincidencia suelta: un aula
    # de unidades donde por casualidad la actividad 3 de la unidad 3 se llame "S3-" no
    # alcanza para redefinir el eje de la materia entera.
    return ("semana" if coincidencias >= 3 else "unidad"), coincidencias


def naturaleza_de(titulo: str, seccion: str, tipo: str) -> str:
    """Qué ES la actividad para el que lee: cadencia, integrador, evaluación o columna
    administrativa. PURA.

    El orden importa. `administrativa` va primero porque la declara la SECCIÓN y le gana
    al título: "Calificación Coloquio" leído solo parece el coloquio, y es la casilla donde
    se vuelca su nota. Después `evaluacion`, porque el Coloquio Final Integrador dice
    "integrador" en el nombre y no es el TAI.
    """
    if _RE_SECCION_ADMIN.search(seccion or ""):
        return ADMINISTRATIVA
    if _RE_EVALUACION.search(titulo or "") or _RE_EVALUACION.search(seccion or ""):
        return EVALUACION
    if _RE_INTEGRADOR.search(titulo or "") or _RE_INTEGRADOR.search(seccion or ""):
        return INTEGRADOR
    return CADENCIA


def catalogo_de_actividades(contenidos: list[dict]) -> dict:
    """Lee la estructura del curso y devuelve qué actividad calificable es cada cosa.

    PURA: recibe lo que devuelve `core_course_get_contents` y no consulta nada.

    -> {"items": [...], "por_cmid": {cmid: item}, "unidades": [...], "semanas": [...],
        "eje": "unidad"|"semana", "avisos": [...]}

    Cada item: `cmid`, `titulo`, `modname`, `tipo`, `naturaleza`, `unidad`, `semana`,
    `seccion`, `orden`, `nro`. `unidad`/`semana` en None significan **no se pudo leer**,
    nunca cero.
    """
    eje, coincidencias = eje_de_secciones(contenidos)
    items: list[dict] = []
    avisos: list[str] = []
    sin_eje: list[str] = []
    unidad_actual: int | None = None
    semana_actual: int | None = None
    huerfanas_de_seccion: list[str] = []
    semana_sin_unidad = False

    for sec in contenidos or []:
        nombre = sec.get("name") or ""
        n = _norm(nombre)
        m = _RE_SECCION_UNIDAD.match(n)
        if m:
            numero = int(m.group(1))
            if eje == "semana":
                semana_actual = numero
                # El repaso cierra varias unidades y el parcial las evalúa: ninguna de las
                # dos semanas ES de una unidad. Heredar la última abierta metería el Primer
                # Parcial adentro de la unidad 2.
                semana_sin_unidad = bool(_RE_SEMANA_SIN_UNIDAD.search(n))
            else:
                unidad_actual = numero
        elif n not in _SUBBLOQUES:
            # Sección con nombre propio que no es un sub-bloque: corta la herencia. Es lo
            # que deja afuera a COLOQUIOS y a los integradores en Matemática.
            if eje == "unidad":
                if unidad_actual is not None:
                    huerfanas_de_seccion.append(nombre)
                unidad_actual = None

        for i, mod in enumerate(sec.get("modules") or []):
            tipo = TIPOS.get(mod.get("modname"))
            if tipo is None:
                continue  # etiquetas, urls, certificados: no se califican
            cmid = mod.get("id")
            if cmid is None:
                continue
            titulo = mod.get("name") or f"cmid {cmid}"
            leido = titulos.leer(titulo)

            # El foro de consultas de una unidad es el único mojón de unidad que tiene el
            # aula de PyE. Se lee ANTES de resolver la actividad para que la propia unidad
            # que abre valga desde acá.
            if eje == "semana" and tipo == "foro" and _RE_ES_CONSULTA.search(titulo):
                mu = _RE_UNIDAD_MENCION.search(titulo)
                if mu:
                    unidad_actual = int(mu.group(1))

            if eje == "semana":
                semana = leido.semana if leido.semana is not None else semana_actual
                unidad = leido.unidad
                if unidad is None and not semana_sin_unidad:
                    unidad = unidad_actual
            else:
                semana = leido.semana
                unidad = unidad_actual
                # Precedencia: el título gana cuando declara la unidad de forma explícita
                # (`U{n}S{m}`). Misma regla que `titulos.py`, y acá además verifica la
                # lectura por sección.
                if leido.unidad is not None:
                    if unidad is not None and unidad != leido.unidad:
                        avisos.append(
                            f"«{titulo}» está en la sección de la unidad {unidad} pero su "
                            f"título declara la unidad {leido.unidad}. Gana el título "
                            "(es explícito); revisá dónde está puesta la actividad.")
                    unidad = leido.unidad

            if (semana if eje == "semana" else unidad) is None:
                sin_eje.append(titulo)

            items.append({
                "cmid": int(cmid),
                "titulo": titulo,
                "modname": mod.get("modname"),
                "tipo": tipo,
                "naturaleza": naturaleza_de(titulo, nombre, tipo),
                "unidad": unidad,
                "semana": semana,
                "seccion": nombre,
                "orden": i,
                "fuera_de_cadencia": leido.fuera_de_cadencia,
            })

    # Orden de lectura: por el EJE de la materia primero, y dentro de él por tipo y por el
    # orden en que están puestas en el aula. Lo que no declara eje va al final y no se
    # mezcla con lo que sí. Ordenar por título dejaba el "Video 4 Semana 1" después del
    # "Video 1 Semana 2".
    def _clave(it):
        eje_v = it["semana"] if eje == "semana" else it["unidad"]
        otro = it["unidad"] if eje == "semana" else it["semana"]
        return (eje_v is None, eje_v or 0,
                ORDEN_TIPOS.index(it["tipo"]) if it["tipo"] in ORDEN_TIPOS else 9,
                otro is None, otro or 0, it["orden"], it["titulo"])

    items.sort(key=_clave)
    for pos, it in enumerate(items):
        it["nro"] = pos + 1  # número de columna en la matriz del PDF

    unidades = sorted({it["unidad"] for it in items if it["unidad"] is not None})
    semanas = sorted({it["semana"] for it in items if it["semana"] is not None})

    avisos.append(
        f"El número de las secciones numeradas de este curso se leyó como {eje.upper()} "
        + (f"({coincidencias} actividades declaran su semana con el prefijo «S«n»-» y "
           "coincide con la sección que las contiene)." if eje == "semana"
           else "(ninguna actividad declara semana con el prefijo «S«n»-», así que el "
                "número de la sección es la unidad).")
        + f" Unidades vistas: {unidades or '—'}. Semanas vistas: "
        + (f"{semanas[0]}-{semanas[-1]}." if semanas else "—."))

    if sin_eje:
        avisos.append(
            f"{len(sin_eje)} actividad(es) calificables no quedaron bajo ninguna "
            f"{'semana' if eje == 'semana' else 'unidad'}: "
            + ", ".join(sin_eje[:6]) + ("…" if len(sin_eje) > 6 else "")
            + ". Se listan igual, agrupadas aparte — no se les inventa una.")
    if huerfanas_de_seccion:
        avisos.append(
            "Estas secciones cortaron la herencia de unidad por tener nombre propio: "
            + ", ".join(sorted(set(huerfanas_de_seccion))[:8])
            + ". Si alguna era un sub-bloque de unidad escrito distinto, sus "
              "actividades quedaron sin unidad (no mal asignadas).")

    return {"items": items, "por_cmid": {it["cmid"]: it for it in items},
            "unidades": unidades, "semanas": semanas, "eje": eje,
            "avisos": avisos}


# ---------------------------------------------------------------------------
# Cruce alumno x actividad. PURO.
# ---------------------------------------------------------------------------

def items_de_calificador(usergrades: list[dict], catalogo: dict) -> dict:
    """La lista REAL de columnas del informe: los grade items que devuelve el calificador,
    con la unidad/semana/naturaleza que les pone el catálogo. PURA.

    Existe porque la estructura del curso **no sabe qué se califica**. Dos casos reales, y
    los dos rompían el informe de maneras distintas:

    - Un `forum` sólo tiene nota si la cátedra lo configuró calificable. En Probabilidad y
      Estadística hay cuatro que sí (22 notas por comisión) y en Matemática ninguno.
      Tomarlos todos del árbol del curso agrega columnas fantasma donde nadie tiene nota;
      tomarlos de acá los incluye exactamente cuando existen.
    - **Un mismo `cmid` puede tener DOS grade items.** Un foro con calificación de foro y
      de valoraciones devuelve `itemnumber` 0 y 1 con el mismo cmid. Indexar por `cmid`
      —que es lo que hacía este módulo— pisaba uno con el otro y perdía la mitad de las
      notas del foro, en silencio. La clave real es el `id` del grade item.

    El `itemtype: course` (el total del curso) no es una actividad del alumno y no entra:
    contarlo le sumaba a todo el mundo una actividad que nadie hace.
    """
    por_cmid = catalogo.get("por_cmid") or {}
    eje = catalogo.get("eje") or "unidad"
    vistos: dict[int, dict] = {}
    cuenta_cmid: dict[int, int] = {}
    sin_catalogo: list[str] = []

    for u in usergrades or []:
        for it in u.get("gradeitems") or []:
            iid = it.get("id")
            cmid = it.get("cmid")
            if iid is None or cmid is None or int(iid) in vistos:
                continue
            meta = por_cmid.get(int(cmid))
            titulo = it.get("itemname") or (meta or {}).get("titulo") or f"cmid {cmid}"
            if meta is None:
                # El calificador trae algo que el árbol del curso no mostró (una actividad
                # oculta, por ejemplo). Se incluye leyendo el título, y se declara: es un
                # dato real y esconderlo sería peor que no poder agruparlo.
                sin_catalogo.append(titulo)
                leido = titulos.leer(titulo)
                meta = {"tipo": "otro",
                        "naturaleza": naturaleza_de(titulo, "", "otro"),
                        "unidad": leido.unidad, "semana": leido.semana,
                        "seccion": "", "orden": 0, "modname": None}
            cuenta_cmid[int(cmid)] = cuenta_cmid.get(int(cmid), 0) + 1
            vistos[int(iid)] = {
                "item_id": int(iid), "cmid": int(cmid),
                "itemnumber": it.get("itemnumber"),
                "titulo": titulo,
                "sobre": _num(it.get("grademax")),
                "tipo": meta.get("tipo"), "naturaleza": meta.get("naturaleza"),
                "unidad": meta.get("unidad"), "semana": meta.get("semana"),
                "seccion": meta.get("seccion"), "modname": meta.get("modname"),
                "orden": meta.get("orden", 0),
            }

    # Cuando un cmid trae dos items, el título solo no los distingue: en la leyenda del PDF
    # salían dos columnas con el mismo nombre y nadie podía saber cuál era cuál.
    for it in vistos.values():
        if cuenta_cmid.get(it["cmid"], 0) > 1 and it["itemnumber"] is not None:
            it["titulo"] = "{} [nota {}]".format(it["titulo"], int(it["itemnumber"]) + 1)

    items = sorted(vistos.values(), key=lambda it: (
        (it["semana"] if eje == "semana" else it["unidad"]) is None,
        (it["semana"] if eje == "semana" else it["unidad"]) or 0,
        ORDEN_TIPOS.index(it["tipo"]) if it["tipo"] in ORDEN_TIPOS else 9,
        it["orden"], it["titulo"]))
    for pos, it in enumerate(items):
        it["nro"] = pos + 1

    avisos = []
    if sin_catalogo:
        avisos.append(
            f"{len(sin_catalogo)} item(s) del calificador no están en el árbol del curso "
            "(actividades ocultas, o borradas del aula pero no del libro): "
            + ", ".join(sin_catalogo[:5]) + ". Se incluyen igual, con la unidad que diga "
            "su título.")
    dobles = [c for c, n in cuenta_cmid.items() if n > 1]
    if dobles:
        avisos.append(
            f"{len(dobles)} actividad(es) tienen MÁS DE UNA columna de nota en el "
            "calificador (típico del foro calificado: una por el foro y otra por las "
            "valoraciones). Se muestran las dos, numeradas.")

    return {"items": items, "por_id": {it["item_id"]: it for it in items},
            "unidades": sorted({it["unidad"] for it in items if it["unidad"] is not None}),
            "semanas": sorted({it["semana"] for it in items if it["semana"] is not None}),
            "eje": eje, "avisos": avisos}


def filas_de_alumnos(usergrades: list[dict], items_cal: dict,
                     accesos: dict | None = None) -> list[dict]:
    """Una fila por alumno con sus notas ya clasificadas. PURA.

    `items_cal` es lo que devuelve `items_de_calificador` — la lista real de columnas.
    `accesos`, el `{userid: {...}}` del padrón (los dos relojes). Si un alumno no está en
    `accesos` la fila lo dice con `sin_dato`, no con un cero: "nunca abrió la materia" y
    "no pude leer cuándo la abrió" son personas distintas y sólo a una hay que escribirle.
    """
    por_id = items_cal.get("por_id") or {}
    eje = items_cal.get("eje") or "unidad"
    accesos = accesos or {}
    filas = []

    for u in usergrades or []:
        uid = u.get("userid")
        if uid is None:
            continue
        uid = int(uid)
        notas: dict[int, dict] = {}
        por_tipo: dict[str, dict] = {}
        por_eje: dict[int, dict] = {}
        por_naturaleza: dict[str, dict] = {}

        for it in u.get("gradeitems") or []:
            meta = por_id.get(it.get("id"))
            if meta is None:
                continue
            tipo, nat = meta["tipo"], meta["naturaleza"]
            crudo = _num(it.get("graderaw"))
            sobre = _num(it.get("grademax")) or meta.get("sobre")
            eje_v = meta["semana"] if eje == "semana" else meta["unidad"]

            for balde, clave in ((por_tipo, tipo), (por_naturaleza, nat)):
                b = balde.setdefault(clave, {"hechas": 0, "total": 0, "suma_pct": 0.0})
                b["total"] += 1
            if eje_v is not None:
                b = por_eje.setdefault(eje_v, {"hechas": 0, "total": 0})
                b["total"] += 1

            if crudo is None:
                continue  # no la hizo: la celda queda VACÍA, no en cero
            pct = (crudo / sobre * 100) if sobre else None
            por_tipo[tipo]["hechas"] += 1
            por_naturaleza[nat]["hechas"] += 1
            if pct is not None:
                por_tipo[tipo]["suma_pct"] += pct
                por_naturaleza[nat]["suma_pct"] += pct
            if eje_v is not None:
                por_eje[eje_v]["hechas"] += 1
            notas[int(meta["item_id"])] = {
                "item_id": meta["item_id"], "cmid": meta["cmid"], "nro": meta["nro"],
                "titulo": meta["titulo"], "tipo": tipo, "naturaleza": nat,
                "unidad": meta["unidad"], "semana": meta["semana"],
                "nota": round(crudo, 2), "sobre": sobre,
                "porcentaje": round(pct, 1) if pct is not None else None,
                "texto": limpiar_nota(it.get("gradeformatted")),
                "calificado_ts": int(it.get("gradedategraded") or 0),
            }

        for balde in (por_tipo, por_naturaleza):
            for b in balde.values():
                b["promedio_pct"] = (round(b["suma_pct"] / b["hechas"], 1)
                                     if b["hechas"] else None)
                b.pop("suma_pct", None)

        hechas = sum(b["hechas"] for b in por_tipo.values())
        # Hasta dónde llegó ESTE alumno en el eje de la materia. Es lo que separa al que
        # no arrancó del que venía y paró, y ningún promedio lo muestra.
        llego = max((k for k, b in por_eje.items() if b["hechas"]), default=None)
        filas.append({
            "userid": uid,
            "nombre": u.get("userfullname") or "",
            "notas": notas,
            "por_tipo": por_tipo,
            "por_naturaleza": por_naturaleza,
            "por_eje": por_eje,
            "eje": eje,
            "ultima_con_actividad": llego,
            "actividades_con_nota": hechas,
            "sin_actividad": hechas == 0,
            **_acceso(accesos.get(uid) or {}),
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


def resumen_de_comision(filas: list[dict], items_cal: dict) -> dict:
    """Los números del encabezado del informe. PURO.

    No hay porcentaje de avance y es deliberado — la misma decisión, con la misma
    evidencia, que hizo que `avance_alumnos` no lo tuviera: un porcentaje necesita saber
    cuántas actividades ya deberían estar hechas y ese dato no existe (32 de los 36
    cuestionarios de Probabilidad y Estadística no tienen fecha de cierre; ninguna
    actividad de Matemática tiene fecha de entrega). Se cuenta lo que pasó, no se estima lo
    que faltaría.
    """
    items = items_cal.get("items", [])
    total_por_tipo: dict[str, int] = {}
    total_por_naturaleza: dict[str, int] = {}
    for it in items:
        total_por_tipo[it["tipo"]] = total_por_tipo.get(it["tipo"], 0) + 1
        total_por_naturaleza[it["naturaleza"]] = total_por_naturaleza.get(it["naturaleza"], 0) + 1
    hechas_por_tipo = {t: sum((f["por_tipo"].get(t) or {}).get("hechas", 0) for f in filas)
                       for t in total_por_tipo}
    hechas_por_nat = {n: sum((f["por_naturaleza"].get(n) or {}).get("hechas", 0) for f in filas)
                      for n in total_por_naturaleza}
    con_actividad = sum(1 for f in filas if not f["sin_actividad"])
    return {
        "alumnos": len(filas),
        "con_actividad": con_actividad,
        "sin_actividad": len(filas) - con_actividad,
        "actividades_del_curso": total_por_tipo,
        "actividades_por_naturaleza": total_por_naturaleza,
        "notas_cargadas": hechas_por_tipo,
        "notas_por_naturaleza": hechas_por_nat,
        "sin_dato_de_acceso": sum(1 for f in filas if f["estado_aula"] == "sin_dato"),
        "nunca_abrieron_la_materia": sum(1 for f in filas
                                         if f["estado_aula"] == "nunca_abrio"),
    }


def hasta_donde_llego_el_curso(filas_de_todas: list[dict]) -> int | None:
    """La última unidad/semana con actividad registrada en TODO el curso. PURA.

    Es la respuesta a "¿hasta dónde va la cursada?" cuando el campus no la da. Y no la da:
    32 de los 36 cuestionarios de Probabilidad y Estadística no tienen fecha de cierre, y
    los seis primeros ni siquiera fecha de apertura. Sin esto el informe muestra 16 semanas
    donde 11 están vacías **por calendario** y se leen como abandono — el mismo error que
    ya cometimos mostrando exámenes de noviembre como "nadie entregó".

    Se mide contra lo que el curso REALMENTE hizo, que es el único patrón disponible, y el
    informe lo declara como derivado en vez de presentarlo como un dato del campus.
    """
    vistas = [f["ultima_con_actividad"] for f in filas_de_todas
              if f.get("ultima_con_actividad") is not None]
    return max(vistas) if vistas else None


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

    Las columnas del informe salen del CALIFICADOR y no del árbol del curso (ver
    `items_de_calificador`), así que se arman después de la primera lectura y valen para
    todas las comisiones: el libro de calificaciones es del curso, no del grupo.
    """
    secciones, err = await contenidos(client, course_id)
    if err:
        return {"error": err}
    cat = catalogo_de_actividades(secciones)

    sem = asyncio.Semaphore(_SEM)

    async def _una(c):
        async with sem:
            return c, await notas_de_comision(client, course_id, c["group_id"])

    crudos, avisos = [], list(cat["avisos"])
    for res in await asyncio.gather(*(_una(c) for c in comisiones),
                                    return_exceptions=True):
        if isinstance(res, BaseException):
            avisos.append(f"Una comisión no se pudo leer: {type(res).__name__}: {res}")
            continue
        crudos.append(res)

    # Las columnas se arman con TODAS las comisiones juntas y no con la primera: si a la
    # primera le falta un item (una actividad restringida a otro grupo, una lectura
    # incompleta), esa columna desaparecería del informe de las quince.
    todos = [u for _, n in crudos for u in (n.get("usergrades") or [])]
    items_cal = items_de_calificador(todos, cat)
    avisos.extend(items_cal["avisos"])
    if not items_cal["items"]:
        return {"error": "el calificador de este curso no devolvió ninguna actividad con "
                         "nota. Puede ser que tu rol no tenga permiso sobre el libro de "
                         "calificaciones: no es lo mismo que un curso sin actividad.",
                "avisos": avisos}

    bloques = []
    for c, notas in crudos:
        if notas.get("error"):
            avisos.append(f"{c['comision']}: {notas['error']}")
            bloques.append({**c, "error": notas["error"], "alumnos": []})
            continue
        accesos = (padrones.get(c["group_id"], {}) or {}).get("accesos") or {}
        filas = filas_de_alumnos(notas["usergrades"], items_cal, accesos)
        bloques.append({**c, "alumnos": filas,
                        "resumen": resumen_de_comision(filas, items_cal)})

    # Hasta dónde llegó la cursada, medido sobre el curso entero y no sobre una comisión:
    # una comisión atrasada correría el punto de referencia para todas las demás.
    hasta = hasta_donde_llego_el_curso([f for b in bloques for f in (b.get("alumnos") or [])])
    if hasta is not None:
        eje = items_cal["eje"]
        tope = (items_cal["semanas"] or items_cal["unidades"] or [hasta])[-1]
        avisos.append(
            f"La cursada va por la {eje.upper()} {hasta} de {tope}. Es un dato DERIVADO "
            f"(la última {eje} con alguna nota cargada en el curso), no una fecha del "
            "campus — el campus no la expone. Lo que está vacío más adelante está vacío "
            "por calendario, no por atraso.")

    return {"ok": True, "course_id": course_id, "catalogo": items_cal,
            "estructura": cat, "hasta_donde_llego": hasta,
            "comisiones": bloques, "avisos": avisos}
