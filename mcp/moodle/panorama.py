"""Panorama del curso por comisión — la vista del PROFESOR, no la del tutor.

El resto de la skill responde *"¿qué me falta a MÍ?"*: una comisión, la del tutor logueado.
Esto responde *"¿dónde hay trabajo parado en el curso, y de quién es esa comisión?"* sobre
las 16 comisiones a la vez.

REGLA HEREDADA de `auditoria.py`, y es la que ordena todo el módulo: **se verifica el
TRABAJO, nunca se califica a la PERSONA.** Acá se NOMBRA al tutor de cada comisión — eso es
un dato de ruteo, a quién llamar, y sin él el tablero no sirve para nada — pero no se emite
ranking, puntaje ni juicio sobre él. Las filas son hechos; la conclusión la saca el profesor.

Cuatro cosas se verificaron en vivo el 2026-08-07 contra Prog I (course 74, 16 comisiones,
553 alumnos) y este módulo está construido alrededor de ellas:

1. **El profesor VE las comisiones ajenas.** `core_enrol_get_enrolled_users` y los WS de
   assign devuelven datos de grupos que no son del usuario logueado. Sin eso nada de esto
   existiría, así que es lo primero que la tool reporta si falla.

2. **Las entregas vienen INFLADAS.** `mod_assign_get_submissions` devolvió 46 registros para
   la unidad 1 cuando el conteo oficial de Moodle era 25. Los otros 21 están en estado
   `new`: el alumno abrió la tarea y nunca entregó. Contarlos le mostraba al profesor casi
   el doble de trabajo del que hay. Solo cuenta `status == "submitted"`.

3. **El `groupid` de la entrega viene 0.** Cada registro de entrega trae ese campo y era
   tentador resolver las 16 comisiones con una sola consulta agrupando por él: vino 0 en los
   46. No es "grupo 0" — es "no aplica", porque la tarea no es de entrega grupal. Otro cero
   que significa "no sé". Por eso el alumno se cruza a su comisión por PADRÓN, que cuesta una
   consulta por comisión y sí es un dato real.

4. **El rol del tutor NO es siempre el mismo.** 15 de las 16 comisiones tienen a su tutor
   como `editingteacher`; C1-14 lo tiene como `teacher`. Filtrar por una lista blanca de
   roles dejaba una comisión de 35 alumnos reportada como huérfana — un hallazgo falso, en
   el tablero del profesor, sobre una persona. Se toma **todo el que NO es `student`**,
   misma doctrina que leer la escala por su texto y no por su número.

Todo acá es READ-ONLY sobre Moodle: ninguna función de este módulo escribe en el campus.
"""

import asyncio
import json
import re
import statistics
import time
from pathlib import Path

from .cliente import MoodleWSError
from .ws_api import (
    _AULA_DESENGANCHE_DIAS,
    _AULA_NUNCA,
    _AULA_SIN_DATO,
    _RE_CONSULTAS,
    _RE_NO_DOCENTE,
    _es_estudiante,
    _fila_aula,
    _instanceid,
    _orden_aula,
    leer_foro,
    listar_foros,
)

# El campus es compartido con 25 tutores y ~500 alumnos: no lo martillamos. Mismo techo
# que usan `buscar_alumnos` y `foros_pendientes`.
_SEM = 5

# Etiqueta de comisión del campus TUP: "A26 C1-06", "M26 C2-01", "A25 C3-14". La letra es
# el cuatrimestre de INGRESO del alumno (Agosto/Marzo) y no el del aula — ese gotcha ya está
# documentado en comisiones.json y acá solo hay que no tropezarlo.
# Lo que NO matchea a propósito: los grupos regionales ("R-Rosario", "R-Chubut") y los
# auxiliares ("Grupo_2", "Entrego_1er_examen"), que no son comisiones de tutoría. Nunca se
# descartan en silencio: vuelven en `grupos_ignorados`.
_RE_COMISION = re.compile(r"^\s*([A-Z]\d{2})\s+C(\d+)\s*-\s*(\d+)\s*$", re.I)

# Moodle guarda "sin calificar" como -1 en el campo `grade` (llega como "-1.00000").
_SIN_CALIFICAR = -1.0

# QUÉ CUENTA COMO CORREGIDO. Va por `gradingstatus` de la ENTREGA, no por la existencia de
# un registro de nota — y esa distinción costó un bug real (2026-08-07, primera corrida):
# `mod_assign_get_grades` devuelve una fila con grade `-1.00000` para las entregas que
# TODAVÍA NO se corrigieron. Tomar "hay registro de nota" como "está corregida" mandaba las
# 10 pendientes del curso al balde de `calificado_sin_nota` y el tablero mostraba CERO
# pendientes en las 16 comisiones. El peor error posible acá: decirle al profesor que está
# todo al día cuando no lo está.
#
# El cruce en vivo salió perfecto y por eso se confía en este campo:
#     ('graded','1.00000')->13   ('graded','2.00000')->2   ('notgraded','-1.00000')->10
# y ese 10 coincide exacto con el conteo oficial de `sumario`.
#
# `released` entra también: es el estado del flujo de calificación cuando la nota ya se le
# publicó al alumno. Los intermedios (`inmarking`, `readyforreview`, ...) NO entran: la nota
# existe pero el alumno todavía no la tiene, así que el trabajo no está cerrado.
_ESTADOS_CORREGIDA = {"graded", "released"}

# Cuántos puntos porcentuales tiene que despegarse una comisión del curso para
# llamarla atrasada. Sin margen sale marcada la mitad de la cohorte por estar a un
# punto de la media, y una lista con la mitad adentro no la mira nadie.
_MARGEN_ATRASO_PP = 10


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ts(x) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _dias(desde_ts: int, hasta_ts: int) -> float | None:
    """Días entre dos timestamps, redondeado a una decimal. `None` si alguno no está.

    Devuelve `None` —y no 0— cuando falta un extremo, porque un 0 acá se leería como
    "se corrigió el mismo día" cuando en realidad es "no sé cuándo". Es la misma regla que
    ordena toda la skill: "no sé" nunca se disfraza de dato.
    """
    if desde_ts <= 0 or hasta_ts <= 0 or hasta_ts < desde_ts:
        return None
    return round((hasta_ts - desde_ts) / 86400, 1)


def _etiqueta_comision(nombre: str) -> str | None:
    """'A26 C1-06' -> 'com6'. `None` si el grupo no es una comisión de tutoría.

    Se devuelve la forma corta porque es la que usan el reparto interno, `mi_comision` y
    los tutores al hablar. El nombre completo del campus viaja igual en cada fila.
    """
    m = _RE_COMISION.match(nombre or "")
    if not m:
        return None
    return f"com{int(m.group(3))}"


# ---------------------------------------------------------------------------
# Padrón: quién es alumno y quién es docente en cada comisión.
# ---------------------------------------------------------------------------

# Lo que se guarda de cada alumno para poder medir desenganche sin pedir nada de nuevo.
_CAMPOS_ACCESO = ("id", "fullname", "email", "lastaccess", "lastcourseaccess", "groups")

# La REGIONAL del alumno sale de sus grupos, no de su perfil. Cada alumno está en dos grupos:
# su comisión ("M25 C4-03") y su sede ("R-San Nicolás"), y los dos vienen en el `groups` de
# `core_enrol_get_enrolled_users`. Hay 17 regionales por curso.
#
# El camino tentador era `city` (la ciudad del perfil): NO sirve. Viene en el 71% de los
# alumnos, es texto libre y trae "Córdoba" y "Cordoba" como valores distintos. Sería una
# regional inventada en tres de cada diez filas.
#
# Los grupos `R-*` son justamente los que `_RE_COMISION` descarta como comisión y devuelve en
# `grupos_ignorados`: lo que para una mitad del módulo es ruido, para ésta es el dato.
_RE_REGIONAL = re.compile(r"^\s*R\s*-\s*(.+?)\s*$")


def _regional_de(u: dict) -> str | None:
    """Regional (sede) del alumno, de sus grupos. `None` si no está en ninguna `R-*`."""
    for g in u.get("groups") or []:
        m = _RE_REGIONAL.match(g.get("name") or "")
        if m:
            return m.group(1)
    return None


async def _padron_comision(client, course_id: int, group_id: int) -> dict:
    """Matriculados de UNA comisión, separados en alumnos y docentes.

    Es la misma request que usa `_alumnos_de_comision`, pero acá interesa el descarte de
    aquélla: los que NO son alumnos son el/los docentes a cargo. No se filtra por un rol
    concreto (ver la trampa 4 del encabezado del módulo): docente = todo el que no es
    `student`. Un matriculado sin roles declarados cuenta como alumno, que es el lado
    conservador del error — nombrar a alguien como responsable de una comisión por un
    campo vacío sería justo el invento que la skill no hace.
    """
    try:
        us = await client.ws(
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
        return {"error": f"no pude leer el padrón: {e.errorcode}"}
    if not isinstance(us, list):
        return {"error": "el padrón no vino como lista"}

    alumnos: dict[int, str] = {}
    docentes: list[dict] = []
    accesos: dict[int, dict] = {}
    for u in us:
        uid = u.get("id")
        if uid is None:
            continue
        if _es_estudiante(u):
            alumnos[int(uid)] = u.get("fullname") or ""
            # Los dos relojes de acceso vienen en ESTA misma respuesta y se estaban tirando.
            # Guardarlos no cuesta ninguna request extra, y es lo que le faltaba a la vista
            # del profesor: hasta ahora medía el trabajo de corrección de los tutores y no
            # tenía una sola columna sobre alumnos que se están yendo.
            # Se copian sólo los campos que hacen falta y se PRESERVA LA AUSENCIA: si
            # `lastcourseaccess` no vino, acá tampoco está, y `_lectura_aula` lo va a leer
            # como `sin_dato` en vez de como "nunca abrió".
            accesos[int(uid)] = {k: u[k] for k in _CAMPOS_ACCESO if k in u}
        else:
            docentes.append({
                "nombre": u.get("fullname") or "",
                "userid": int(uid),
                "rol": "/".join(r.get("shortname", "?") for r in u.get("roles", [])),
            })
    return {"alumnos": alumnos, "docentes": docentes, "accesos": accesos}


async def _padrones(client, course_id: int, comisiones: list[dict]) -> tuple[dict, list[str]]:
    """Padrón de todas las comisiones, en paralelo. -> ({group_id: padrón}, avisos)."""
    sem = asyncio.Semaphore(_SEM)

    async def _una(c):
        async with sem:
            return c["group_id"], await _padron_comision(client, course_id, c["group_id"])

    out, avisos = {}, []
    for res in await asyncio.gather(*(_una(c) for c in comisiones), return_exceptions=True):
        if isinstance(res, BaseException):
            avisos.append(f"Una comisión no se pudo leer: {type(res).__name__}: {res}")
            continue
        gid, padron = res
        out[gid] = padron
        if padron.get("error"):
            avisos.append(f"group_id {gid}: {padron['error']}")
    return out, avisos


async def _padron_del_curso(client, course_id: int) -> dict:
    """Padrón COMPLETO del curso, en una sola consulta, para poder cuadrarlo.

    Existe por un pedido concreto de coordinación: el informe decía "555 alumnos en comisiones"
    y nadie podía saber si ése era el total del curso. Ahora se contrasta — y en la primera
    corrida aparecieron **11 alumnos de Prog I matriculados en el curso y en su regional pero
    en NINGUNA comisión**. A ésos no los ve ningún tutor, porque toda la skill (y todas las
    vistas del campus que usa un tutor) trabajan por comisión. Eran invisibles por construcción.

    Cuesta una consulta más y no es gratis (11 s en un curso de 604 matriculados), así que si
    falla se devuelve el error y el informe declara el hueco en vez de romperse.
    """
    try:
        us = await client.ws("core_enrol_get_enrolled_users",
                             {"courseid": course_id,
                              "options": [{"name": "onlyactive", "value": 1}]})
    except MoodleWSError as e:
        return {"error": f"no pude leer el padrón completo del curso: {e.errorcode}"}
    if not isinstance(us, list):
        return {"error": "el padrón del curso no vino como lista"}

    alumnos, sueltos = {}, []
    for u in us:
        if not _es_estudiante(u):
            continue
        uid = u.get("id")
        if uid is None:
            continue
        alumnos[int(uid)] = u
        if not any(_etiqueta_comision(g.get("name") or "") for g in (u.get("groups") or [])):
            sueltos.append(u)
    return {"total_alumnos": len(alumnos), "sueltos": sueltos}


async def _comisiones_del_curso(client, course_id: int) -> tuple[list[dict], list[str], str | None]:
    """Comisiones de tutoría del curso. -> (comisiones, grupos_ignorados, error)."""
    try:
        gs = await client.ws("core_group_get_course_groups", {"courseid": course_id})
    except MoodleWSError as e:
        return [], [], f"No pude listar los grupos del curso {course_id}: {e.errorcode}"
    comisiones, ignorados = [], []
    for g in gs or []:
        nombre = g.get("name") or ""
        etiqueta = _etiqueta_comision(nombre)
        if etiqueta is None:
            ignorados.append(nombre)
            continue
        comisiones.append({"comision": etiqueta, "group_id": g.get("id"), "nombre": nombre})
    comisiones.sort(key=lambda c: int(c["comision"][3:]))
    return comisiones, ignorados, None


# ---------------------------------------------------------------------------
# Desenganche del CURSO entero (una fila por alumno, no por comisión).
# ---------------------------------------------------------------------------

_SIN_COMISION = "(sin comisión)"
_SIN_REGIONAL = "(sin regional)"


def desenganche_del_curso(padrones: dict, comisiones: list[dict],
                          dias_desenganche: int = _AULA_DESENGANCHE_DIAS,
                          sueltos: list[dict] | None = None) -> dict:
    """PURA: quién dejó de abrir ESTA materia en todo el curso. Sin red — trabaja sobre los
    padrones que ya se bajaron para el panorama, así que no cuesta ninguna request.

    Es la única parte de la vista del profesor que habla de ALUMNOS. Todo el resto del módulo
    mide trabajo de corrección, o sea lo que deben los tutores; el profesor no tenía forma de
    ver "esta comisión tiene 12 pibes que nunca abrieron la materia".

    **Se mide con el reloj de la materia, no con el del campus, y la diferencia es enorme.**
    Un informe de coordinación real (Prog III, 13/08) listaba "7 alumnos inactivos" cortando
    por 21+ días sin pisar el CAMPUS. Aplicado ese mismo criterio a los 119 alumnos de las
    comisiones de Juani encontraba 3 de los 30 que están desenganchados de la materia: se
    perdía el 90%, y siempre para el lado que tranquiliza — porque al que entra todos los días
    para otra materia y no abre ésta, el reloj del campus lo muestra impecable.

    Devuelve SÓLO a los desenganchados y a los `sin_dato`, no al padrón entero: la lista es
    para actuar. Cuántos quedaron afuera y por qué se declara en `relevados` y `al_dia`, para
    que el recorte no se lea como cobertura total.

    `sueltos` son los alumnos del curso que **no están en ninguna comisión** (de
    `_padron_del_curso`). Se miden igual, con la etiqueta `(sin comisión)`: son los que ninguna
    vista por comisión alcanza, así que si además dejaron de entrar no había nadie que lo notara.
    """
    filas: list[dict] = []
    por_comision: dict[str, dict] = {}
    # Por REGIONAL, y esto el informe original no lo tenía: si el desenganche se concentra en
    # una sede, el problema puede no ser de los alumnos ni del tutor (una cohorte que arrancó
    # tarde, una sede con un problema de matriculación). Es ruteo —a qué sede preguntarle—,
    # no un puntaje de sedes: por eso va con el total al lado, porque 3 de 4 y 3 de 60 no son
    # lo mismo y un ranking crudo los mostraría igual.
    por_regional: dict[str, dict] = {}
    relevados = al_dia = 0
    avisos: list[str] = []

    grupos = [(c["comision"], padrones.get(c["group_id"]) or {}) for c in comisiones]
    if sueltos:
        # Los que no están en ninguna comisión entran como un grupo más, para que se los mida
        # con la misma vara en vez de quedar afuera del relevamiento por no tener dónde caer.
        grupos.append((_SIN_COMISION, {"accesos": {u.get("id"): u for u in sueltos}}))

    for etiqueta, padron in grupos:
        if padron.get("error"):
            avisos.append(f"{etiqueta}: no se pudo leer el padrón, quedó SIN MEDIR "
                          f"({padron['error']}).")
            por_comision[etiqueta] = {"sin_medir": True}
            continue
        accesos = padron.get("accesos")
        if accesos is None:
            avisos.append(f"{etiqueta}: el padrón vino sin datos de acceso, quedó SIN MEDIR.")
            por_comision[etiqueta] = {"sin_medir": True}
            continue

        cuenta = {"alumnos": len(accesos), "desenganchados": 0, "nunca_abrieron": 0,
                  "entran_al_campus_sin_abrir_la_materia": 0, "sin_dato": 0}
        for u in accesos.values():
            relevados += 1
            f = _fila_aula(u, dias_desenganche)
            f["comision"] = etiqueta
            f["regional"] = _regional_de(u)
            reg = por_regional.setdefault(f["regional"] or _SIN_REGIONAL,
                                          {"alumnos": 0, "desenganchados": 0})
            reg["alumnos"] += 1
            if f["estado_aula"] == _AULA_SIN_DATO:
                cuenta["sin_dato"] += 1
            elif f["desenganchado_de_la_materia"]:
                cuenta["desenganchados"] += 1
                reg["desenganchados"] += 1
                if f["estado_aula"] == _AULA_NUNCA:
                    cuenta["nunca_abrieron"] += 1
                if f["entra_al_campus_sin_abrir_la_materia"]:
                    cuenta["entran_al_campus_sin_abrir_la_materia"] += 1
            else:
                al_dia += 1
                continue
            filas.append(f)
        por_comision[etiqueta] = cuenta

    # Orden: primero los que ELIGEN no entrar (aparecen por el campus y no abren la materia).
    # Es el grupo más accionable y el más recuperable: no perdieron el acceso, no abrieron.
    # El que hace 200 días que no aparece por Moodle es más extremo pero también más perdido,
    # y sobre todo es OTRO problema — va después, con sus días a la vista.
    filas.sort(key=lambda f: (0 if f["entra_al_campus_sin_abrir_la_materia"] else 1,
                              _orden_aula(f)))

    # AGRUPADO POR REGIONAL, pedido de coordinación (13/08): el seguimiento lo hacen los tutores
    # nexos, que trabajan por sede, así que una lista global les obliga a filtrar 150 filas a
    # ojo. Las regionales que más concentran van primero.
    #
    # Adentro de cada regional se mantiene el orden de arriba (primero los que entran al campus
    # y no abren la materia), así el nexo abre SU bloque y los más accionables ya están arriba:
    # el agrupado cambia dónde busca cada uno, no qué es urgente.
    bloques: dict[str, list] = {}
    for f in filas:
        bloques.setdefault(f.get("regional") or _SIN_REGIONAL, []).append(f)
    por_regional_bloques = [
        {"regional": reg,
         "alumnos": (por_regional.get(reg) or {}).get("alumnos"),
         "desenganchados": len(lista),
         "lista": lista}
        for reg, lista in sorted(bloques.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    tot = {
        "desenganchados": sum(1 for f in filas
                              if f["estado_aula"] != _AULA_SIN_DATO
                              and f["desenganchado_de_la_materia"]),
        "entran_al_campus_sin_abrir_la_materia":
            sum(1 for f in filas if f["entra_al_campus_sin_abrir_la_materia"]),
        "nunca_abrieron": sum(1 for f in filas if f["estado_aula"] == _AULA_NUNCA),
        "nunca_entraron_ni_al_campus":
            sum(1 for f in filas if f["estado_aula"] == _AULA_NUNCA
                and f["dias_sin_entrar_al_campus"] is None),
        "sin_dato": sum(1 for f in filas if f["estado_aula"] == _AULA_SIN_DATO),
    }
    if tot["sin_dato"]:
        avisos.append(f"{tot['sin_dato']} alumno(s) vinieron sin el último acceso a la materia: "
                      "van como `sin_dato`, NO como 'nunca abrió'. El relevamiento no cubre a "
                      "todo el padrón.")
    return {
        "dias_desenganche": dias_desenganche,
        "relevados": relevados,
        "al_dia": al_dia,
        "totales": tot,
        "por_comision": por_comision,
        "por_regional": dict(sorted(
            por_regional.items(),
            key=lambda kv: (-kv[1]["desenganchados"], -kv[1]["alumnos"]))),
        "por_regional_bloques": por_regional_bloques,
        "alumnos": filas,
        "criterio": {
            "desenganchado": f"{dias_desenganche}+ días sin abrir la materia, o nunca abierta.",
            "reloj": "días sin abrir ESTA materia (lastcourseaccess). El acceso al campus va "
                     "aparte y NO se usa para el corte: cortar por el campus pierde a los que "
                     "entran todos los días para otra materia (medido: el 90%).",
            "orden": "primero los que entran al campus y no abren la materia (eligieron no "
                     "entrar, es lo más accionable), después por días sin abrirla.",
            "recorte": f"la lista trae {len(filas)} de {relevados} alumnos: los desenganchados "
                       f"y los `sin_dato`. Los otros {al_dia} abrieron la materia hace menos de "
                       f"{dias_desenganche} días.",
        },
        "sin_dato": avisos,
    }


# ---------------------------------------------------------------------------
# Entregas y notas de una tarea, ya cruzadas contra el padrón.
# ---------------------------------------------------------------------------

def clasificar_entregas(subs: dict, notas: dict) -> dict:
    """PURA: decide qué significa cada entrega. Sin red, sin cliente — a propósito.

    Está separada de la request porque es la capa que **interpreta** el dato, y en esta
    skill todos los bugs serios vivieron justo acá (el del `-1` que apagó las 22 pendientes
    del curso, entre otros). Acá se puede testear sin campus ni credenciales.

    Recibe los payloads crudos de `mod_assign_get_submissions` y `mod_assign_get_grades`
    y devuelve, por userid: quién entregó y cuándo, quién está corregido, quién sigue
    pendiente, a quién le falta la calificación y a quién no se pudo clasificar.
    """
    entregas: dict[int, int] = {}
    estado: dict[int, str] = {}
    for a in (subs or {}).get("assignments", []):
        for s in a.get("submissions", []):
            # SOLO `submitted`. Los `new` son alumnos que abrieron la tarea sin entregar:
            # contarlos infla el trabajo pendiente casi al doble (trampa 2 del encabezado).
            if (s.get("status") or "") != "submitted":
                continue
            uid = s.get("userid")
            if uid is None:
                continue
            entregas[int(uid)] = _ts(s.get("timemodified"))
            estado[int(uid)] = (s.get("gradingstatus") or "").lower()

    # Fecha y valor de la nota. El valor solo decide si la calificación quedó VACÍA; quién
    # está corregido y quién no lo decide `gradingstatus` (ver el comentario de
    # _ESTADOS_CORREGIDA: confundir esto ya apagó las 10 pendientes del curso una vez).
    ts_nota: dict[int, int] = {}
    val_nota: dict[int, float | None] = {}
    for a in (notas or {}).get("assignments", []):
        for g in a.get("grades", []):
            uid = g.get("userid")
            if uid is None:
                continue
            uid = int(uid)
            ts = _ts(g.get("timemodified"))
            if ts >= ts_nota.get(uid, 0):   # puede haber varios intentos: vale el último
                ts_nota[uid] = ts
                val_nota[uid] = _num(g.get("grade"))

    calificadas: dict[int, int] = {}   # userid -> ts de la corrección
    sin_nota: set[int] = set()         # corregida pero con la calificación vacía (hallazgo)
    pendientes: set[int] = set()       # entregada y sin corregir
    sin_clasificar: set[int] = set()   # `gradingstatus` ausente o desconocido: NO se asume
    for uid in entregas:
        est = estado.get(uid) or ""
        if est in _ESTADOS_CORREGIDA:
            val = val_nota.get(uid)
            if val is None or val <= _SIN_CALIFICAR:
                # Corregida de verdad (Moodle la saca de la cola) pero sin calificación:
                # el alumno queda sin nota y nadie lo espera. Es el `calificado_sin_nota`
                # que ya detecta `pendientes_por_corregir`. Hallazgo, no corregido.
                sin_nota.add(uid)
            else:
                calificadas[uid] = ts_nota.get(uid, 0)
        elif est:
            pendientes.add(uid)
        else:
            # Sin `gradingstatus` no se sabe. Antes que meterlo en cualquiera de los dos
            # baldes —y falsear el número en la dirección que sea— se cuenta aparte y sube
            # como aviso.
            sin_clasificar.add(uid)
    # `notas` sale porque ya estaba calculado y se tiraba: es lo que necesita el informe de
    # avance para decir aprobado/desaprobado sin pedir NADA de nuevo. Es el valor CRUDO —
    # interpretarlo exige la config de la tarea (`grade_cfg`), porque en este campus la escala
    # 5 va invertida (1=Aprobado, 2=Desaprobado) y un índice suelto se lee al revés.
    return {"entregas": entregas, "calificadas": calificadas, "sin_nota": sin_nota,
            "pendientes": pendientes, "sin_clasificar": sin_clasificar, "notas": val_nota}


async def _tarea_cruda(client, cmid: str) -> dict:
    """Entregas + notas de una tarea, del CURSO ENTERO, en dos requests.

    Dos y no 2×16: los WS de assign devuelven todo el curso de una, así que la comisión se
    resuelve después cruzando el `userid` contra el padrón. Ver la trampa 3 del encabezado.
    La interpretación de lo que llega vive en `clasificar_entregas`, que es pura y testeable.
    """
    inst = await _instanceid(client, str(cmid))
    if inst is None:
        return {"error": f"no encontré la tarea cmid={cmid}"}
    try:
        subs, notas = await asyncio.gather(
            client.ws("mod_assign_get_submissions", {"assignmentids": [inst]}),
            client.ws("mod_assign_get_grades", {"assignmentids": [inst]}),
        )
    except MoodleWSError as e:
        return {"error": f"no pude leer la tarea {cmid}: {e.errorcode}"}
    return clasificar_entregas(subs or {}, notas or {})


# ---------------------------------------------------------------------------
# Consultas de foro sin responder, atribuidas a la comisión de quien preguntó.
# ---------------------------------------------------------------------------

async def _consultas_sin_responder(client, course_id: int, uid_a_comision: dict[int, str],
                                   max_discusiones: int = 200) -> tuple[dict, list[str]]:
    """{comision: cantidad} de consultas con CERO respuestas. -> (conteo, avisos).

    "Sin responder" = 0 réplicas, es decir **no contestó nadie**. Deliberadamente NO se
    intenta decir "el tutor de esa comisión no contestó": el WS de foros no devuelve roles
    y deducir quién es docente por el nombre sería inventar — la misma razón por la que
    `foros_pendientes` se limita a comparar contra el userid del tutor logueado, cosa que
    acá no aplica porque el profesor no es el autor esperado de esas respuestas.

    La comisión sale de QUIÉN PREGUNTÓ, no del `groupid` del hilo: en este campus los foros
    de consulta son del curso entero y todas las discusiones vienen con groupid -1. Eso ya
    rompió una vez (ver el comentario largo en `ws_api.foros_pendientes`).
    """
    fs = await listar_foros(client, course_id)
    if fs.get("error"):
        return {}, [f"No pude listar los foros: {fs['error']}"], {}

    foros = []
    for f in fs.get("foros", []):
        nombre = f.get("nombre") or ""
        # Los foros de avisos son de una vía (el docente publica, nadie espera respuesta
        # suya) y los de "buscar dupla" son entre alumnos: ningún hilo de ahí es trabajo
        # pendiente de un tutor. Mismo criterio que `foros_pendientes`.
        if f.get("tipo") == "news" or "aviso" in nombre.lower():
            continue
        if not _RE_CONSULTAS.search(nombre) or _RE_NO_DOCENTE.search(nombre):
            continue
        if (f.get("discusiones") or 0) > 0:
            foros.append(f)
    if not foros:
        return {}, [], {}

    sem = asyncio.Semaphore(_SEM)

    async def _hilos(f):
        async with sem:
            return await leer_foro(client, f["forum_id"], limite=max_discusiones)

    conteo: dict[str, int] = {}
    sin_respuesta_uid: dict[int, int] = {}
    avisos: list[str] = []
    huerfanas = 0
    for res in await asyncio.gather(*(_hilos(f) for f in foros), return_exceptions=True):
        if isinstance(res, BaseException):
            avisos.append(f"Un foro no se pudo leer: {type(res).__name__}: {res}")
            continue
        if res.get("error"):
            avisos.append(res["error"])
            continue
        for d in res.get("discusiones", []):
            if (d.get("replicas") or 0) > 0:
                continue
            autor = d.get("autor_userid")
            com = uid_a_comision.get(int(autor)) if autor is not None else None
            if com is None:
                # Autor que no está en ninguna comisión de este curso: otro docente, un
                # alumno de un grupo regional, alguien dado de baja. No se reparte a nadie.
                huerfanas += 1
                continue
            conteo[com] = conteo.get(com, 0) + 1
            # Quién preguntó, para el informe de avance: una consulta sin respuesta es un
            # problema que apunta para adentro, y por comisión no se ve a quién le pasó.
            sin_respuesta_uid[int(autor)] = sin_respuesta_uid.get(int(autor), 0) + 1
    if huerfanas:
        avisos.append(
            f"{huerfanas} consulta(s) sin responder no se pudieron atribuir a ninguna "
            "comisión (autor fuera del padrón: otro docente, grupo regional o baja). "
            "No se sumaron a nadie."
        )
    return conteo, avisos, sin_respuesta_uid


# ---------------------------------------------------------------------------
# TOOL 1 — panorama por comisión
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cortes del trabajo de corrección: por ACTIVIDAD y por TUTOR.
# ---------------------------------------------------------------------------

async def _meta_tareas(client, course_id: int) -> tuple[dict, str | None]:
    """{cmid: {titulo, duedate}} de las tareas del curso, en UNA consulta.

    La clave es el **cmid**, no el id de instancia: en esta skill `assign_id` es el cmid, y
    `mod_assign_get_assignments` devuelve los dos campos. Confundirlos hace que no matchee
    ninguna tarea y el informe salga sin títulos — pasó al escribir esto.

    El `duedate` importa más de lo que parece: si vale 0 la actividad NO tiene fecha de
    entrega, y entonces "sin entrega" no se puede distinguir de "todavía no vencía". Medido en
    Prog I: **ninguna** de las 10 actividades de cierre tiene fecha. Por eso el informe lo
    declara en vez de dejarlo implícito.
    """
    try:
        r = await client.ws("mod_assign_get_assignments", {"courseids": [course_id]})
    except MoodleWSError as e:
        return {}, f"no pude leer las fechas de las tareas: {e.errorcode}"
    out = {}
    for c in (r or {}).get("courses", []):
        for a in c.get("assignments", []):
            cmid = a.get("cmid")
            if cmid is None:
                continue
            # `grade` es la config de calificación y viene en esta misma respuesta: <0 es
            # escala (-scaleid), >0 nota numérica sobre ese máximo, 0 sin calificación. Con
            # eso `nota_display` decodifica sin una sola request extra.
            out[str(cmid)] = {"titulo": a.get("name") or f"cmid {cmid}",
                              "duedate": _ts(a.get("duedate")),
                              "grade_cfg": a.get("grade")}
    return out, None


def resumen_por_actividad(datos_tareas: dict, uid_a_comision: dict, meta: dict,
                          ahora: int) -> list[dict]:
    """PURA: una fila por ACTIVIDAD del curso, sumando las comisiones.

    Es la vista que no existía en ningún lado. Por comisión se veía "com15 tiene 3 sin
    corregir"; lo que no se veía es "la actividad de la unidad 4 tiene 40 sin corregir
    repartidas en 9 comisiones", que es un problema de la consigna o del calendario y no de
    nadie en particular. Sale gratis: los datos ya se bajaron para las filas por comisión.

    Sólo cuenta alumnos que están en alguna comisión, igual que las filas: así los totales
    de las dos vistas cierran entre sí.
    """
    filas = []
    for cmid, d in datos_tareas.items():
        m = meta.get(str(cmid)) or {}
        entregadas = corregidas = sin_nota = pendientes = 0
        esperas, comisiones_con_cola = [], set()
        for uid, t_ent in d["entregas"].items():
            com = uid_a_comision.get(uid)
            if com is None:
                continue
            entregadas += 1
            if uid in d["calificadas"]:
                corregidas += 1
            elif uid in d["sin_nota"]:
                sin_nota += 1
            elif uid in d["pendientes"]:
                pendientes += 1
                comisiones_con_cola.add(com)
                esp = _dias(t_ent, ahora)
                if esp is not None:
                    esperas.append(esp)
        due = m.get("duedate") or 0
        filas.append({
            "cmid": str(cmid),
            "titulo": m.get("titulo") or f"cmid {cmid}",
            "vencimiento": ("sin fecha" if not due
                            else ("vencida" if due < ahora else "a futuro")),
            "vence_en_dias": (None if not due else round((due - ahora) / 86400, 1)),
            "entregadas": entregadas,
            "corregidas": corregidas,
            "sin_corregir": pendientes,
            "calificado_sin_nota": sin_nota,
            "comisiones_con_cola": len(comisiones_con_cola),
            "espera_max_dias": max(esperas) if esperas else None,
        })
    # Primero lo que más espera: es lo accionable. Sin cola, por volumen entregado.
    filas.sort(key=lambda f: (-(f["espera_max_dias"] or 0), -f["sin_corregir"]))
    return filas


def carga_por_tutor(filas: list[dict]) -> list[dict]:
    """PURA: la carga de cada tutor sumando SUS comisiones.

    Seis tutores llevan dos comisiones cada uno, así que la cola real de una persona no está
    en ninguna fila: hay que sumarla. Esto la suma.

    **Son hechos de carga y de cola, no un puntaje.** No hay nota, ni podio, ni etiqueta sobre
    la persona: las comisiones no son comparables entre sí —distinto tamaño, distinta consigna,
    distinta cohorte— así que un ranking convertiría un hecho en un juicio que ninguna
    herramienta puede firmar. Se ordena por la espera más antigua porque eso dice por dónde
    empezar, que es para lo que sirve.
    """
    por: dict[str, dict] = {}
    for f in filas:
        nombre = (f.get("tutor") or {}).get("nombre") or "— sin identificar —"
        d = por.setdefault(nombre, {"tutor": nombre, "comisiones": [], "alumnos": 0,
                                    "sin_corregir": 0, "calificado_sin_nota": 0,
                                    "espera_max_dias": None, "consultas_sin_responder": 0})
        d["comisiones"].append(f.get("comision"))
        d["alumnos"] += f.get("alumnos") or 0
        for k in ("sin_corregir", "calificado_sin_nota", "consultas_sin_responder"):
            if f.get(k):
                d[k] += f[k]
        e = f.get("espera_max_dias")
        if e is not None and (d["espera_max_dias"] is None or e > d["espera_max_dias"]):
            d["espera_max_dias"] = e
    salida = list(por.values())
    salida.sort(key=lambda d: (-(d["espera_max_dias"] or 0), -d["sin_corregir"], d["tutor"]))
    return salida


async def panorama_comisiones(client, course_id: int, cmids: list[str] | None = None,
                              incluir_foros: bool = True,
                              padrones: dict | None = None,
                              avisos_padron: list[str] | None = None) -> dict:
    """Una fila por comisión del curso: tutor a cargo, entregas, correcciones pendientes,
    cuánto hace que espera la más vieja y consultas de foro sin responder.

    `cmids` acota a un subconjunto de tareas (por defecto, todas las del curso que estén
    mapeadas en el llamador). `incluir_foros=False` saltea la parte de foros, que es la más
    cara en requests.

    `padrones`/`avisos_padron` permiten inyectar los padrones ya bajados. Existe para que
    `informe_profesor` no pida dos veces la misma cosa: el padrón cuesta una request por
    comisión y son 16.
    """
    t0 = time.time()
    comisiones, ignorados, err = await _comisiones_del_curso(client, course_id)
    if err:
        return {"error": err}
    if not comisiones:
        return {
            "error": f"El curso {course_id} no tiene grupos con formato de comisión.",
            "grupos_ignorados": ignorados,
        }

    if padrones is None:
        padrones, avisos = await _padrones(client, course_id, comisiones)
    else:
        avisos = list(avisos_padron or [])

    # userid -> comision. Un alumno puede figurar en dos grupos; se queda con el primero y
    # se avisa, porque en ese caso su trabajo se le contaría a una comisión sola.
    uid_a_comision: dict[int, str] = {}
    duplicados = 0
    for c in comisiones:
        for uid in (padrones.get(c["group_id"], {}).get("alumnos") or {}):
            if uid in uid_a_comision:
                duplicados += 1
                continue
            uid_a_comision[uid] = c["comision"]
    if duplicados:
        avisos.append(f"{duplicados} alumno(s) figuran en más de una comisión; se contaron "
                      "en la primera. Sus entregas no se duplican entre filas.")

    tareas = list(cmids or [])
    datos_tareas: dict[str, dict] = {}
    if tareas:
        sem = asyncio.Semaphore(_SEM)

        async def _una(cmid):
            async with sem:
                return cmid, await _tarea_cruda(client, cmid)

        for res in await asyncio.gather(*(_una(t) for t in tareas), return_exceptions=True):
            if isinstance(res, BaseException):
                avisos.append(f"Una tarea no se pudo leer: {type(res).__name__}: {res}")
                continue
            cmid, d = res
            if d.get("error"):
                avisos.append(f"cmid {cmid}: {d['error']}")
                continue
            datos_tareas[cmid] = d

    meta_tareas, err_meta = await _meta_tareas(client, course_id)
    if err_meta:
        avisos.append(err_meta + ": las actividades salen sin título ni fecha de entrega.")

    foros_por_comision: dict[str, int] = {}
    foros_ok = False
    if incluir_foros:
        foros_por_comision, av_f, _ = await _consultas_sin_responder(
            client, course_id, uid_a_comision)
        avisos.extend(av_f)
        foros_ok = not any("No pude listar los foros" in a for a in av_f)

    ahora = int(time.time())
    filas = []
    for c in comisiones:
        gid = c["group_id"]
        padron = padrones.get(gid) or {}
        sin_dato: list[str] = []

        if padron.get("error"):
            filas.append({
                **c, "tutor": None, "alumnos": None, "entregados": None,
                "corregidos": None, "sin_corregir": None, "calificado_sin_nota": None,
                "espera_max_dias": None, "demora_mediana_dias": None,
                "consultas_sin_responder": None,
                "sin_dato": [f"No se pudo leer el padrón de esta comisión: {padron['error']}"],
            })
            continue

        alumnos = padron.get("alumnos") or {}
        docentes = padron.get("docentes") or []
        if not docentes:
            # No es un error de lectura: la comisión existe y no tiene docente asignado.
            # Es un HALLAZGO y el profesor lo tiene que ver como tal, no como un blanco.
            tutor = None
            sin_dato.append("Comisión SIN DOCENTE asignado en el campus (hallazgo, no una "
                            "falla de lectura).")
        elif len(docentes) == 1:
            tutor = docentes[0]
        else:
            tutor = docentes[0]
            sin_dato.append("Hay más de un docente en esta comisión: "
                            + ", ".join(f"{d['nombre']} [{d['rol']}]" for d in docentes)
                            + ". Se muestra el primero.")

        entregados = corregidos = sin_nota_n = pendientes = sin_clasificar = 0
        esperas: list[float] = []
        demoras: list[float] = []
        for d in datos_tareas.values():
            for uid, t_ent in d["entregas"].items():
                if uid_a_comision.get(uid) != c["comision"]:
                    continue
                entregados += 1
                if uid in d["calificadas"]:
                    corregidos += 1
                    dm = _dias(t_ent, d["calificadas"][uid])
                    if dm is not None:
                        demoras.append(dm)
                elif uid in d["sin_nota"]:
                    sin_nota_n += 1
                elif uid in d["pendientes"]:
                    pendientes += 1
                    esp = _dias(t_ent, ahora)
                    if esp is not None:
                        esperas.append(esp)
                else:
                    sin_clasificar += 1

        if sin_clasificar:
            sin_dato.append(f"{sin_clasificar} entrega(s) sin estado de corrección legible: "
                            "no se contaron ni como corregidas ni como pendientes.")
        if not alumnos:
            sin_dato.append("La comisión no tiene alumnos matriculados: los ceros de esta "
                            "fila son 'todavía no arrancó', no 'está al día'.")
        elif not tareas:
            sin_dato.append("No se miró ninguna tarea (no se pasaron cmids): las columnas "
                            "de entregas y corrección quedan sin medir.")
        elif entregados == 0:
            sin_dato.append("Nadie entregó todavía en las tareas miradas: el 0 de pendientes "
                            "es 'no hay qué corregir', no 'está al día'.")

        if incluir_foros and not foros_ok:
            sin_dato.append("No se pudieron leer los foros del curso.")

        filas.append({
            **c,
            "tutor": tutor,
            "alumnos": len(alumnos),
            "entregados": entregados if tareas else None,
            "corregidos": corregidos if tareas else None,
            "sin_corregir": pendientes if tareas else None,
            "calificado_sin_nota": sin_nota_n if tareas else None,
            "espera_max_dias": max(esperas) if esperas else None,
            "demora_mediana_dias": round(statistics.median(demoras), 1) if demoras else None,
            "consultas_sin_responder": (foros_por_comision.get(c["comision"], 0)
                                        if (incluir_foros and foros_ok) else None),
            "sin_dato": sin_dato,
        })

    por_actividad = resumen_por_actividad(datos_tareas, uid_a_comision, meta_tareas, ahora)
    sin_fecha = [a for a in por_actividad if a["vencimiento"] == "sin fecha"]
    if sin_fecha and len(sin_fecha) == len(por_actividad) and por_actividad:
        # No es un detalle: sin `duedate` no hay forma de distinguir "no entregó" de "todavía
        # no vencía", y eso es lo que hace que `alumnos_en_riesgo` marque al padrón entero.
        avisos.append(f"NINGUNA de las {len(por_actividad)} actividades miradas tiene fecha de "
                      "entrega. Con eso, 'sin entrega' no se puede distinguir de 'todavía no "
                      "vencía': cuidado al leer cualquier conteo de faltantes como abandono.")
    elif sin_fecha:
        avisos.append(f"{len(sin_fecha)} de {len(por_actividad)} actividades no tienen fecha de "
                      "entrega.")

    return {
        "ok": True,
        "course_id": course_id,
        "comisiones": len(filas),
        "tareas_miradas": len(datos_tareas),
        "tareas_pedidas": len(tareas),
        "filas": filas,
        "por_actividad": por_actividad,
        "por_tutor": carga_por_tutor(filas),
        "actividades_sin_fecha_de_entrega": len(sin_fecha),
        "grupos_ignorados": ignorados,
        "avisos": avisos,
        "segundos": round(time.time() - t0, 1),
        "lectura": ("Las filas son HECHOS del campus, no una evaluación del tutor. Un número "
                    "alto puede ser una comisión más grande, una consigna más difícil o una "
                    "semana de examen. Mirá `sin_dato` antes de sacar cualquier conclusión: "
                    "un 0 con motivo NO es lo mismo que un 0 de trabajo al día."),
    }


# ---------------------------------------------------------------------------
# TOOL 2 — demora de corrección
# ---------------------------------------------------------------------------

async def demora_correccion(client, course_id: int, cmids: list[str]) -> dict:
    """Cuánto espera un alumno desde que entrega hasta que le cargan la nota, por comisión.

    Es el número que de verdad describe el trabajo de tutoría, y el que el conteo de
    pendientes NO distingue: 20 pendientes entregados ayer están bien; 3 esperando hace tres
    semanas están mal.

    Devuelve dos cosas distintas y no hay que confundirlas:
      - `demora_*`: sobre las entregas YA corregidas. Es historia, ya pasó.
      - `espera_*`: sobre las que siguen SIN corregir, contra hoy. Es lo que un alumno está
        esperando ahora mismo, y es lo accionable.
    """
    t0 = time.time()
    comisiones, ignorados, err = await _comisiones_del_curso(client, course_id)
    if err:
        return {"error": err}
    if not cmids:
        return {"error": "Necesito al menos un cmid de tarea para medir la demora."}

    padrones, avisos = await _padrones(client, course_id, comisiones)
    uid_a_comision: dict[int, str] = {}
    for c in comisiones:
        for uid in (padrones.get(c["group_id"], {}).get("alumnos") or {}):
            uid_a_comision.setdefault(uid, c["comision"])

    sem = asyncio.Semaphore(_SEM)

    async def _una(cmid):
        async with sem:
            return cmid, await _tarea_cruda(client, cmid)

    por_comision: dict[str, dict] = {c["comision"]: {"demoras": [], "esperas": []}
                                     for c in comisiones}
    ahora = int(time.time())
    miradas = 0
    for res in await asyncio.gather(*(_una(t) for t in cmids), return_exceptions=True):
        if isinstance(res, BaseException):
            avisos.append(f"Una tarea no se pudo leer: {type(res).__name__}: {res}")
            continue
        cmid, d = res
        if d.get("error"):
            avisos.append(f"cmid {cmid}: {d['error']}")
            continue
        miradas += 1
        for uid, t_ent in d["entregas"].items():
            com = uid_a_comision.get(uid)
            if com is None:
                continue
            if uid in d["calificadas"]:
                dm = _dias(t_ent, d["calificadas"][uid])
                if dm is not None:
                    por_comision[com]["demoras"].append(dm)
            elif uid in d["pendientes"]:
                esp = _dias(t_ent, ahora)
                if esp is not None:
                    por_comision[com]["esperas"].append(esp)

    filas = []
    for c in comisiones:
        acum = por_comision[c["comision"]]
        dems, esps = acum["demoras"], acum["esperas"]
        docentes = (padrones.get(c["group_id"], {}) or {}).get("docentes") or []
        filas.append({
            "comision": c["comision"],
            "group_id": c["group_id"],
            "tutor": docentes[0] if docentes else None,
            "corregidas": len(dems),
            "demora_mediana_dias": round(statistics.median(dems), 1) if dems else None,
            "demora_max_dias": max(dems) if dems else None,
            "sin_corregir": len(esps),
            "espera_mediana_dias": round(statistics.median(esps), 1) if esps else None,
            "espera_max_dias": max(esps) if esps else None,
            "sin_dato": ([] if (dems or esps) else
                         ["Sin entregas medibles en las tareas miradas: no hay demora que "
                          "calcular. No significa que la corrección esté al día."]),
        })

    return {
        "ok": True,
        "course_id": course_id,
        "tareas_miradas": miradas,
        "filas": filas,
        "grupos_ignorados": ignorados,
        "avisos": avisos,
        "segundos": round(time.time() - t0, 1),
        "lectura": ("`demora_*` es historia (entregas ya corregidas); `espera_*` es lo que un "
                    "alumno está esperando AHORA. Para actuar mirá `espera_max_dias`."),
    }




# ---------------------------------------------------------------------------
# TOOL 3 — informe de NEXOS: sólo alumnos, por regional, con su tutor nexo
# ---------------------------------------------------------------------------

_NEXOS_PATH = Path(__file__).resolve().parent.parent / "nexos.json"


async def _nombre_del_curso(client, course_id: int) -> str | None:
    """Nombre real del curso, del campus. `None` si no se pudo leer — nunca se inventa."""
    try:
        r = await client.ws("core_course_get_courses_by_field",
                            {"field": "id", "value": course_id})
    except MoodleWSError:
        return None
    cs = (r or {}).get("courses") or []
    return (cs[0].get("fullname") or None) if cs else None


def nexos_por_regional() -> tuple[dict, str | None]:
    """Catálogo de Tutores Nexo por regional. -> (regionales, aviso).

    La clave es el nombre del grupo del campus SIN el prefijo `R-`: el campus tiene
    "R-San Nicolás" y el catálogo "San Nicolás". Verificado el 2026-08-13: las 17 regionales
    son idénticas en los cuatro cursos y las 17 tienen nexo, sin sobrantes de ningún lado.

    Si el archivo no está o no se puede leer, devuelve `{}` y un aviso: el informe sale igual
    pero **sin** nexos y diciéndolo. Un catálogo que falla no puede inventar un responsable —
    es la misma regla que resuelve el par comisión→tutor en vivo en vez de leerlo de un JSON.
    """
    try:
        doc = json.loads(_NEXOS_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {}, (f"No pude leer el catálogo de nexos ({type(e).__name__}): el informe sale "
                    "sin los datos de contacto. Revisá mcp/nexos.json.")
    regs = doc.get("regionales") or {}
    if not regs:
        return {}, "El catálogo de nexos está vacío: el informe sale sin datos de contacto."
    return regs, None


async def informe_nexos(client, course_id: int,
                        dias_desenganche: int = _AULA_DESENGANCHE_DIAS) -> dict:
    """Los alumnos que dejaron de abrir la materia, por REGIONAL, con su Tutor Nexo.

    Es el informe para los **nexos**, y a propósito no trae una sola columna del trabajo de
    corrección de los tutores. Antes estaban los dos en el mismo PDF y eso tenía un costo que
    no era de formato: un tutor que abre un documento donde su comisión aparece medida al lado
    de una lista de alumnos lo lee como que lo están evaluando a él. Son dos informes con dos
    destinatarios — los alumnos van al nexo de su sede, el trabajo docente va a coordinación
    (`panorama_comisiones`) — y separarlos es lo que deja hablar de cada cosa sin ruido.

    De paso sale más barato: sin la mitad de correcciones no hace falta consultar NINGUNA
    tarea. Son las comisiones, un padrón por comisión y el padrón del curso, y nada más.

    Todo read-only.
    """
    t0 = time.time()
    comisiones, ignorados, err = await _comisiones_del_curso(client, course_id)
    if err:
        return {"error": err}
    if not comisiones:
        return {"error": f"El curso {course_id} no tiene grupos con formato de comisión.",
                "grupos_ignorados": ignorados}

    padrones, avisos_padron = await _padrones(client, course_id, comisiones)
    curso_entero = await _padron_del_curso(client, course_id)
    deseng = desenganche_del_curso(padrones, comisiones, dias_desenganche,
                                   sueltos=curso_entero.get("sueltos"))

    # El nexo de cada bloque. Si una regional no está en el catálogo, el bloque sale SIN nexo
    # y se declara: nunca se le adjudica a alguien una sede que no es suya.
    catalogo, aviso_cat = nexos_por_regional()
    sin_nexo, sin_regional = [], 0
    for b in deseng.get("por_regional_bloques", []):
        if b["regional"] == _SIN_REGIONAL:
            # No es una regional: es el balde de los que no están en ningún grupo `R-*`.
            # Contarlo como "regional sin nexo en el catálogo" sería mandar a revisar un
            # renombre que no existe — y marcaba el informe como degradado por nada.
            b["nexo"] = None
            sin_regional = b["desenganchados"]
            continue
        datos_nexo = catalogo.get(b["regional"])
        b["nexo"] = datos_nexo or None
        if not datos_nexo:
            sin_nexo.append(b["regional"])

    huecos = list(deseng["sin_dato"]) + list(avisos_padron)
    if aviso_cat:
        huecos.append(aviso_cat)
    if sin_nexo:
        huecos.append("Sin Tutor Nexo en el catálogo: " + ", ".join(sin_nexo)
                      + ". Esos bloques salen sin contacto — revisá si la regional se "
                        "renombró en el campus o si falta cargarla en mcp/nexos.json.")
    if sin_regional:
        huecos.append(f"{sin_regional} alumno(s) de la lista no están en ningún grupo de "
                      "regional (`R-*`), así que no tienen nexo a quién derivarlos. Van en el "
                      "bloque «(sin regional)».")

    en_comisiones = sum(len((padrones.get(c["group_id"]) or {}).get("alumnos") or {})
                        for c in comisiones)
    sueltos = curso_entero.get("sueltos") or []
    if curso_entero.get("error"):
        padron = {"en_comisiones": en_comisiones, "total_del_curso": None, "cuadra": None,
                  "sin_comision": None}
        huecos.append(f"No se pudo cuadrar el padrón contra el total del curso "
                      f"({curso_entero['error']}): puede haber alumnos sin comisión que este "
                      "informe no ve.")
    else:
        total = curso_entero["total_alumnos"]
        padron = {
            "en_comisiones": en_comisiones,
            "total_del_curso": total,
            "sin_comision": len(sueltos),
            "cuadra": (en_comisiones + len(sueltos)) == total,
            "alumnos_sin_comision": [
                {"nombre": u.get("fullname"), "userid": u.get("id"),
                 "email": (u.get("email") or "").lower(), "regional": _regional_de(u)}
                for u in sueltos],
        }
        if sueltos:
            huecos.append(f"{len(sueltos)} alumno(s) están matriculados en el curso pero en "
                          "NINGUNA comisión: no los ve ningún tutor. Van medidos con la "
                          "etiqueta (sin comisión).")
        if not padron["cuadra"]:
            huecos.append(f"El padrón NO cuadra: {en_comisiones} en comisiones + "
                          f"{len(sueltos)} sin comisión ≠ {total} del curso. Puede haber "
                          "alguien matriculado en dos comisiones (contado dos veces).")

    return {
        "ok": True,
        "course_id": course_id,
        "curso": await _nombre_del_curso(client, course_id),
        "comisiones": len(comisiones),
        "regionales": len(deseng.get("por_regional_bloques") or []),
        "padron": padron,
        "hechos": {
            "alumnos_relevados": f"{padron['en_comisiones']}/{padron['total_del_curso']}"
                                 if padron.get("total_del_curso") else deseng["relevados"],
            "alumnos_sin_comision": padron.get("sin_comision"),
            "no_abren_la_materia": deseng["totales"]["desenganchados"],
            "entran_al_campus_sin_abrir_la_materia":
                deseng["totales"]["entran_al_campus_sin_abrir_la_materia"],
            "nunca_abrieron_la_materia": deseng["totales"]["nunca_abrieron"],
            "sin_dato": deseng["totales"]["sin_dato"],
        },
        "desenganche": deseng,
        "grupos_ignorados": ignorados,
        "_meta": {
            "fuente": "vivo",
            "segundos": round(time.time() - t0, 1),
            "nexos_en_catalogo": len(catalogo),
            "regionales_sin_nexo": sin_nexo,
            "alumnos_sin_regional": sin_regional,
            "degradado": bool(deseng["sin_dato"] or sin_nexo or aviso_cat or avisos_padron
                              or curso_entero.get("error")),
            "sin_dato": huecos,
        },
        "lectura": (
            "Este informe habla de ALUMNOS y de nadie más: no trae ni una columna del trabajo "
            "de corrección de los tutores (eso es `panorama_comisiones`). Los días son SIN "
            "ABRIR ESTA MATERIA, no sin entrar al campus — medir por el del campus pierde a la "
            "mayoría, porque el que entra todos los días para otra materia aparece impecable. "
            "«Nunca abrió la materia» NO es abandono confirmado: puede haberse matriculado "
            "esta semana y acá no se ve la fecha de matriculación."
        ),
    }


# ---------------------------------------------------------------------------
# TOOL 4 — avance de los alumnos: cuánto entregaron y cómo les fue, por comisión
# ---------------------------------------------------------------------------

def nota_texto(grade_cfg, valor) -> str | None:
    """Puente a `ws_api.nota_display`: valor crudo -> 'Aprobado' / '8.5' / None.

    Se importa acá y no se reimplementa porque esa función es la que sabe que **la escala 5
    de la TUP va invertida** (1=Aprobado, 2=Desaprobado) y que una escala desconocida NO se
    devuelve como número crudo. Reimplementarlo sería la forma más rápida de leer un
    desaprobado como aprobado.
    """
    from .ws_api import nota_display
    return nota_display(grade_cfg, None if valor is None else f"{valor:.5f}")


def _aprobo(texto: str | None) -> bool | None:
    """¿Esa nota es aprobatoria? `None` si no se puede decidir — nunca se asume.

    Se lee por TEXTO, no por número, que es la regla del dominio. Ojo con el substring:
    "satisfactorio" está dentro de "no satisfactorio", así que el chequeo del negativo va
    PRIMERO y es el que manda.
    """
    if not texto or texto.startswith("⚠️"):
        return None
    t = texto.strip().lower()
    if t.startswith("no ") or t.startswith("desaprobado"):
        return False
    if t in ("aprobado", "satisfactorio", "supera lo esperado"):
        return True
    try:  # numérica: en la TUP se aprueba con 6
        return float(t.replace(",", ".")) >= 6
    except ValueError:
        return None


def avance_de_alumnos(datos_tareas: dict, meta: dict, padrones: dict, comisiones: list[dict],
                      foros_por_uid: dict | None = None,
                      dias_desenganche: int = _AULA_DESENGANCHE_DIAS) -> dict:
    """PURA: cuánto entregó y cómo le fue a cada alumno, agrupado por comisión.

    **No calcula un "porcentaje de avance", y es una decisión, no una omisión.** Un porcentaje
    necesita un denominador —"las actividades que ya deberían estar"— y ese dato NO existe en
    este campus: 35 de 42 actividades de Prog III y 10 de 11 de Prog I no tienen `duedate`.
    El atajo tentador es "actividades en juego = las que tienen al menos una entrega", y está
    medido que falla: en Prog I las unidades 6, 7 y 8 tienen **exactamente una** entrega cada
    una, así que un alumno adelantado pondría media materia "en juego" y dejaría a los otros
    565 como atrasados. Con un umbral más alto (20% del padrón) en Prog I no entra ninguna
    actividad y todos quedan en 0/0.

    Entonces se compara contra lo que el curso REALMENTE hizo: cada alumno contra la **mediana
    de su comisión**, cada comisión contra la **mediana del curso**. "Entregó 0 donde la
    mediana de su comisión es 4" es un hecho; "va al 35% del plan" sería un número inventado.

    El otro cruce, que es el que contesta *por qué* una comisión está atrasada: se mira el
    reloj de la materia junto con las entregas. Atrasada **con** los alumnos entrando es una
    cosa (están ahí y no entregan: consigna, acompañamiento); atrasada **porque** se fueron es
    otra. Por comisión, ninguna de las dos mitades sola lo distingue.
    """
    # userid -> comisión, y el crudo de cada alumno para el reloj de la materia.
    uid_com: dict[int, str] = {}
    crudo: dict[int, dict] = {}
    for c in comisiones:
        p = padrones.get(c["group_id"]) or {}
        for uid, u in (p.get("accesos") or {}).items():
            uid_com.setdefault(int(uid), c["comision"])
            crudo.setdefault(int(uid), u)

    # Entregas y notas por alumno, sobre las tareas relevadas.
    ent: dict[int, int] = {}
    aprob: dict[int, int] = {}
    desap: dict[int, int] = {}
    sin_nota_al: dict[int, int] = {}
    esperando: dict[int, float] = {}   # uid -> días que espera su entrega más vieja sin nota
    import time as _t
    ahora = int(_t.time())
    for cmid, d in datos_tareas.items():
        cfg = (meta.get(str(cmid)) or {}).get("grade_cfg")
        for uid, t_ent in d["entregas"].items():
            uid = int(uid)
            if uid not in uid_com:
                continue
            ent[uid] = ent.get(uid, 0) + 1
            if uid in d.get("sin_nota", ()):
                sin_nota_al[uid] = sin_nota_al.get(uid, 0) + 1
            elif uid in d.get("pendientes", ()):
                esp = _dias(t_ent, ahora)
                if esp is not None and esp > esperando.get(uid, -1):
                    esperando[uid] = esp
            elif uid in d.get("calificadas", {}):
                ok = _aprobo(nota_texto(cfg, (d.get("notas") or {}).get(uid)))
                if ok is True:
                    aprob[uid] = aprob.get(uid, 0) + 1
                elif ok is False:
                    desap[uid] = desap.get(uid, 0) + 1

    foros_por_uid = foros_por_uid or {}
    filas: list[dict] = []
    for uid, com in uid_com.items():
        u = crudo.get(uid) or {}
        estado_aula, dias_aula = None, None
        f_aula = _fila_aula(u, dias_desenganche) if u else {}
        filas.append({
            "userid": uid,
            "nombre": f_aula.get("nombre") or u.get("fullname"),
            "email": f_aula.get("email") or (u.get("email") or "").lower(),
            "comision": com,
            "regional": _regional_de(u),
            "entregas": ent.get(uid, 0),
            "aprobadas": aprob.get(uid, 0),
            "desaprobadas": desap.get(uid, 0),
            "sin_nota": sin_nota_al.get(uid, 0),
            "espera_correccion_dias": esperando.get(uid),
            "consultas_sin_responder": foros_por_uid.get(uid, 0),
            "estado_aula": f_aula.get("estado_aula"),
            "dias_sin_abrir_la_materia": f_aula.get("dias_sin_abrir_la_materia"),
            "entra_al_campus_sin_abrir_la_materia":
                f_aula.get("entra_al_campus_sin_abrir_la_materia"),
        })

    def _mediana(vals):
        return round(statistics.median(vals), 1) if vals else None

    todas = [f["entregas"] for f in filas]
    mediana_curso = _mediana(todas)
    hay_movimiento = max(todas or [0]) > 0
    # La proporción de alumnos SIN NINGUNA entrega es el comparador que sirve en cualquier
    # momento del cuatrimestre. La mediana no: probado en Prog I, con 437 de 566 alumnos en
    # cero la mediana del curso es 0, ninguna comisión queda "por debajo" y marcaba 16 de 16 —
    # la misma saturación de la racha, ahora en este informe. Con proporciones, Prog I da una
    # sola comisión despegada del resto (94% contra 77% del curso), que es una lista accionable.
    pct_cero_curso = (round(100 * sum(1 for f in filas if f["entregas"] == 0) / len(filas))
                      if filas else None)

    por_comision = []
    for c in comisiones:
        de_com = [f for f in filas if f["comision"] == c["comision"]]
        if not de_com:
            continue
        vals = [f["entregas"] for f in de_com]
        med = _mediana(vals)
        deseng = sum(1 for f in de_com if f["estado_aula"] == _AULA_NUNCA
                     or (f["dias_sin_abrir_la_materia"] or 0) >= dias_desenganche)
        en_cero = sum(1 for f in de_com if f["entregas"] == 0)
        pct_cero = round(100 * en_cero / len(de_com))

        # Atrasada = se despega del curso, por proporción de alumnos en cero o por mediana.
        # El margen existe para que no salga marcada media cohorte por estar a un punto.
        atrasada = bool(
            (pct_cero_curso is not None and pct_cero >= pct_cero_curso + _MARGEN_ATRASO_PP)
            or (med is not None and mediana_curso is not None and mediana_curso > 0
                and med < mediana_curso))

        # El *por qué*: atrasada con los alumnos presentes no es lo mismo que atrasada porque
        # se fueron. Es lectura, no veredicto: dice qué mirar, no quién tiene la culpa.
        if not hay_movimiento:
            lectura = "nadie entregó todavía en el curso: no hay con qué comparar"
        elif not atrasada:
            lectura = (f"en línea con el curso ({pct_cero}% sin entregar, "
                       f"{pct_cero_curso}% en el curso)")
        elif deseng > len(de_com) / 3:
            lectura = (f"atrasada y con muchos alumnos que no abren la materia "
                       f"({pct_cero}% sin entregar contra {pct_cero_curso}% del curso)")
        else:
            lectura = (f"atrasada con los alumnos entrando: entregan poco, no es que se fueron "
                       f"({pct_cero}% sin entregar contra {pct_cero_curso}% del curso)")
        por_comision.append({
            "comision": c["comision"],
            "alumnos": len(de_com),
            "entregas_mediana": med,
            "entregas_max": max(vals),
            "alumnos_en_cero": en_cero,
            "pct_sin_entregar": pct_cero,
            "desaprobadas": sum(f["desaprobadas"] for f in de_com),
            "no_abren_la_materia": deseng,
            "esperando_correccion": sum(1 for f in de_com if f["espera_correccion_dias"]),
            "atrasada": atrasada,
            "lectura": lectura,
        })
    # Primero la que más se despega: por proporción sin entregar, no por mediana.
    por_comision.sort(key=lambda c: (-c["pct_sin_entregar"], -c["alumnos_en_cero"]))

    # Los que menos entregaron, del curso entero. Se ordena por entregas y después por días
    # sin abrir la materia: el que entregó poco Y no aparece es el más urgente.
    flojos = sorted(filas, key=lambda f: (f["entregas"],
                                          -(f["dias_sin_abrir_la_materia"] or 0)))
    return {
        "alumnos": len(filas),
        "entregas_mediana_curso": mediana_curso,
        "pct_sin_entregar_curso": pct_cero_curso,
        # Si más de la mitad del curso no entregó nada, la mediana es 0 y NO sirve para
        # comparar comisiones. Se declara para que nadie la lea como si discriminara.
        "mediana_no_discrimina": bool(mediana_curso == 0 and hay_movimiento),
        "alumnos_en_cero": sum(1 for f in filas if f["entregas"] == 0),
        "con_desaprobadas": sum(1 for f in filas if f["desaprobadas"]),
        "esperando_correccion": sum(1 for f in filas if f["espera_correccion_dias"]),
        "por_comision": por_comision,
        "flojos": flojos,
        "criterio": {
            "comparador": "La comisión se compara por PROPORCIÓN de alumnos sin ninguna "
                          "entrega, no por mediana: con el curso arrancando la mediana da 0 en "
                          "todas y no discrimina (Prog I: 437 de 566 en cero).",
            "sin_porcentaje": "No hay % de avance a propósito: sin fechas de entrega no existe "
                              "un denominador honesto. Cada alumno se compara contra la mediana "
                              "de su comisión y cada comisión contra la del curso.",
            "matriculacion": "NO se puede saber cuándo se matriculó un alumno (la API no lo "
                             "expone, verificado). El que entró tarde entregó menos y no es "
                             "peor alumno: no lo leas como desempeño.",
            "aprobado": "La nota se lee por TEXTO de la escala, nunca por el número: la escala "
                        "5 de este campus va invertida (1=Aprobado, 2=Desaprobado).",
        },
    }


async def avance_alumnos(client, course_id: int, cmids: list[str] | None = None,
                         dias_desenganche: int = _AULA_DESENGANCHE_DIAS,
                         incluir_foros: bool = True) -> dict:
    """Cómo van los ALUMNOS con las entregas, por comisión. Read-only.

    La tercera vista del curso, y responde lo que las otras dos no: `informes_nexos` mira si
    el alumno aparece, `panorama_comisiones` si el tutor corrigió, y ésta si el alumno
    **entrega y aprueba**. Cruza el avance con el reloj de la materia, que es lo que permite
    decir por qué una comisión está atrasada en vez de sólo que lo está.
    """
    t0 = time.time()
    comisiones, ignorados, err = await _comisiones_del_curso(client, course_id)
    if err:
        return {"error": err}
    if not comisiones:
        return {"error": f"El curso {course_id} no tiene grupos con formato de comisión.",
                "grupos_ignorados": ignorados}

    padrones, avisos = await _padrones(client, course_id, comisiones)
    meta, err_meta = await _meta_tareas(client, course_id)
    if err_meta:
        avisos.append(err_meta + ": las actividades salen sin título ni tipo de nota.")

    tareas = list(cmids or [])
    datos_tareas: dict[str, dict] = {}
    if tareas:
        sem = asyncio.Semaphore(_SEM)

        async def _una(cmid):
            async with sem:
                return cmid, await _tarea_cruda(client, cmid)

        for res in await asyncio.gather(*(_una(t) for t in tareas), return_exceptions=True):
            if isinstance(res, BaseException):
                avisos.append(f"Una tarea no se pudo leer: {type(res).__name__}: {res}")
                continue
            cmid, d = res
            if d.get("error"):
                avisos.append(f"cmid {cmid}: {d['error']}")
                continue
            datos_tareas[cmid] = d
    if not datos_tareas:
        avisos.append("No se pudo relevar NINGUNA actividad: las entregas de todos quedan en 0 "
                      "por falta de datos, no porque nadie haya entregado.")

    foros_por_uid: dict[int, int] = {}
    if incluir_foros:
        uid_com = {int(uid): c["comision"] for c in comisiones
                   for uid in ((padrones.get(c["group_id"]) or {}).get("alumnos") or {})}
        _, av_f, foros_por_uid = await _consultas_sin_responder(client, course_id, uid_com)
        avisos.extend(av_f)

    av = avance_de_alumnos(datos_tareas, meta, padrones, comisiones, foros_por_uid,
                           dias_desenganche)

    sin_fecha = sum(1 for c in (meta or {}).values() if not c.get("duedate"))
    if sin_fecha:
        avisos.append(f"{sin_fecha} de {len(meta)} actividades no tienen fecha de entrega: por "
                      "eso este informe NO calcula un porcentaje de avance.")

    return {
        "ok": True,
        "course_id": course_id,
        "curso": await _nombre_del_curso(client, course_id),
        "comisiones": len(comisiones),
        "tareas_miradas": len(datos_tareas),
        "tareas_pedidas": len(tareas),
        **av,
        "grupos_ignorados": ignorados,
        "_meta": {
            "fuente": "vivo",
            "segundos": round(time.time() - t0, 1),
            "degradado": bool(avisos),
            "sin_dato": avisos,
        },
        "lectura": (
            "Mide ALUMNOS: cuánto entregaron y cómo les fue. No hay porcentaje de avance porque "
            "sin fechas de entrega no hay denominador honesto — cada alumno va contra la mediana "
            "de su comisión. El que se matriculó tarde entregó menos y NO es peor alumno: la API "
            "no expone la fecha de matriculación. La `lectura` de cada comisión dice qué mirar, "
            "no de quién es la culpa."
        ),
    }
