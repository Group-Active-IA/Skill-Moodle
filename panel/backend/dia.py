"""
El relevamiento del día: qué hay en cada comisión propia, ahora.

Es lo que el panel muestra al abrir, antes de que el tutor escriba nada. Sale de
`sumario`, que es el conteo oficial de Moodle, una llamada por combinación
actividad × comisión. Nada de esto lo calcula el panel y nada lo produce el
modelo: acá sólo se juntan respuestas de tools y se cuentan.

Dos cosas que hacen la diferencia entre esto y un tablero cualquiera:

- **Un `0` puede significar "no hay nada" o "no se pudo relevar", y son
  opuestos.** Cada combinación que falla se cuenta aparte, en `fallaron`, y la
  comisión queda marcada `degradado`. La pantalla NO puede decir "al día" sobre
  un relevamiento con huecos.
- **Toda foto lleva su hora.** Un panel que muestra un número sin decir de cuándo
  es, miente en cuanto pasan diez minutos.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from . import datos  # noqa: F401  (pone `mcp/` en sys.path — dejalo antes de `moodle`)
from moodle import titulos  # noqa: E402

# Etiqueta de columna que trae unidad y, si la materia la usa, semana: `U4` / `U4S2`.
_RE_ETIQUETA = re.compile(r"^U(\d{1,2})(?:S(\d{1,2}))?$")

# El campus no tiene rate limiting declarado y la skill lo tiene anotado como
# pendiente. Seis en paralelo baja el relevamiento de un minuto a unos segundos
# sin castigar al servidor de la facultad.
CONCURRENCIA = 6

# Cuánto vale la foto antes de volver a pedirla. El trabajo de corrección se
# mueve en horas, no en segundos.
TTL_S = 600

_cache: dict[str, Any] = {"foto": None, "at": 0.0}


async def _sumario_seguro(sem: asyncio.Semaphore, assign: dict, com: dict) -> dict:
    async with sem:
        try:
            r = await datos.sumario(assign["assign_id"], com["group_id"])
            return {
                "ok": True,
                "assign_id": assign["assign_id"],
                "titulo": assign["titulo"],
                "participantes": r.get("participantes"),
                "enviados": r.get("enviados"),
                "pendientes": r.get("pendientes"),
            }
        except Exception as exc:
            # No se convierte en 0. Se declara.
            return {
                "ok": False,
                "assign_id": assign["assign_id"],
                "titulo": assign["titulo"],
                "motivo": str(exc)[:200],
            }


def _unidad(titulo: str) -> str:
    """
    Etiqueta corta para el encabezado de una columna.

    Es SÓLO una etiqueta: el título real viaja siempre al lado y es lo que se
    muestra al pasar el mouse. Acá interesa que sea corta y que no mienta.

    Ojo con el caso que ya rompió una vez: Prog IV tiene **dos** actividades de
    la unidad 1 (Fundamentos Spring Boot y APIs REST). Las dos dan `U1`, y dos
    columnas con el mismo nombre son dos columnas que el lector no puede
    distinguir. Esta función no lo resuelve —no puede, la unidad es la misma—;
    lo resuelve quien arma la grilla, agregando el orden. Lo que sí se hace acá
    es no devolver nunca un texto largo que reviente la columna.
    """
    bajo = titulo.lower()

    if "integrador" in bajo:
        return "TI"
    if "recuperatorio" in bajo:
        return "REC"
    if "parcial" in bajo:
        return "PAR"

    # QUÉ dice el título lo resuelve `titulos.py`, que es el mismo criterio que usan
    # la racha de abandono y la columna RETRASO del informe. Acá vivía una CUARTA
    # copia que buscaba "práctica" y se quedaba con SU número: en Matemática eso
    # devolvía el número del cuadernillo y no el de la unidad, y daba mal 13 de 15
    # ("ENTREGA U2S1: ... de la Práctica 1" -> U1). No era un blanco sospechoso:
    # la grilla mostraba cuatro columnas "U1·n" que se leían como cuatro
    # actividades de la unidad 1.
    etiqueta = titulos.etiqueta(titulo)
    if etiqueta:
        return etiqueta

    # El caso que ya no cubre nada: "práctico 3" sin la palabra unidad. Se mantiene
    # porque es como Prog III nombra parte de sus actividades.
    for palabra in ("práctico", "practico", "práctica", "practica"):
        if palabra in bajo:
            resto = bajo.split(palabra, 1)[1].strip()
            numero = "".join(c for c in resto[:3] if c.isdigit())
            if numero:
                return f"U{numero}"

    # Sin patrón reconocible: las iniciales de las dos primeras palabras con
    # contenido. Corto y evidentemente una abreviatura, así que nadie lo lee
    # como un dato.
    palabras = [p for p in titulo.split() if len(p) > 2][:2]
    return "".join(p[0].upper() for p in palabras) or "?"


def orden_actividad(titulo: str) -> tuple[int, int, str]:
    """
    Orden de lectura de las columnas: como cursa el alumno, no como las numeró
    Moodle.

    Ordenar por `assign_id` parece neutral y no lo es: en Prog IV los parciales
    tienen id más bajo que la unidad 1, así que la grilla arrancaba por el
    parcial y ponía la U1 después de la U10. El número interno no es el orden de
    la cursada.

    Primero las unidades por número, después el integrador, después los
    parciales, y al final lo que no se pudo clasificar (alfabético, para que al
    menos sea estable entre corridas).
    """
    etiqueta = _unidad(titulo)
    if etiqueta == "TI":
        return (1, 0, 0, titulo)
    if etiqueta in ("PAR", "REC"):
        return (2, 0, 0, titulo)

    # `U4` y también `U4S2`: la SEMANA es un segundo eje de orden, no ruido. Sin ella
    # las tres entregas de la U5 de Matemática quedaban ordenadas por el texto del
    # título, o sea al azar respecto de la cursada.
    m = _RE_ETIQUETA.match(etiqueta)
    if m:
        return (0, int(m.group(1)), int(m.group(2) or 0), titulo)
    return (3, 0, 0, titulo)


async def _padron_de(sem: asyncio.Semaphore, curso: dict, com: dict) -> dict:
    """¿El padrón de las tareas cuadra con la matrícula del grupo?

    Una consulta por COMISIÓN, no por actividad: el padrón del grupo es el mismo para las 11
    o 15 tareas, así que preguntarlo una vez alcanza y evita multiplicar por 15 el costo.

    Existe porque el conteo de una tarea es internamente consistente aunque le falte gente:
    `sumario` dice 36 y no hay nada que delate que hay 37 matriculados. Medido el 2026-08-18:
    dos alumnos así en cuatro comisiones, uno ausente de las once actividades de su curso.
    """
    primera = (curso.get("tareas") or [None])[0]
    if not primera:
        return {"verificado": False, "motivo": "el curso no tiene actividades cargadas"}
    async with sem:
        try:
            r = await datos.server.entregas_tarea(primera["assign_id"], com["group_id"])
        except Exception as exc:
            return {"verificado": False, "motivo": str(exc)[:160]}
    padron = r.get("padron")
    if not padron:
        return {"verificado": False, "motivo": "la tarea no devolvió el cuadre del padrón"}
    return padron


async def relevar() -> dict:
    """Relevamiento completo. Tarda: es una llamada por actividad y comisión."""
    crudo = await datos.mis_datos()
    cuerpo = crudo.get("datos", crudo)
    cursos = cuerpo.get("cursos", [])

    sem = asyncio.Semaphore(CONCURRENCIA)
    trabajos: list[tuple[dict, dict, dict]] = []
    # Comisiones cuyo curso NO tiene `tareas` en el snapshot de "Mis datos". Antes esto
    # las dejaba afuera del todo: una comisión real (con `comisiones_del_tutor`) que
    # desaparecía sin ningún aviso, indistinguible de "al día" — el mismo blind spot que
    # el propio DESIGN.md del panel dice que es el bug más caro del proyecto, en un
    # estado que ni ahí está modelado. Detectado en vivo el 2026-08-31.
    sin_tareas: list[tuple[dict, dict]] = []

    for curso in cursos:
        # El orden de las columnas se fija UNA vez, acá, y lo heredan la grilla
        # del mapa y la ficha de cada comisión. Dos vistas con las columnas en
        # distinto orden se leen como datos distintos.
        tareas = sorted(curso.get("tareas", []), key=lambda t: orden_actividad(t["titulo"]))
        for com in curso.get("comisiones_del_tutor", []):
            if not tareas:
                sin_tareas.append((curso, com))
                continue
            for assign in tareas:
                trabajos.append((curso, com, assign))

    # Los sumarios y, en paralelo, el cuadre del padrón de cada comisión.
    pares_comision = [
        (curso, com)
        for curso in cursos
        for com in curso.get("comisiones_del_tutor", [])
    ]
    resultados, padrones = await asyncio.gather(
        asyncio.gather(*(_sumario_seguro(sem, assign, com) for _, com, assign in trabajos)),
        asyncio.gather(*(_padron_de(sem, curso, com) for curso, com in pares_comision)),
    )
    cuadres = {
        (curso["course_id"], com["group_id"]): p
        for (curso, com), p in zip(pares_comision, padrones)
    }

    comisiones: dict[tuple[int, int], dict] = {}

    def _fila_base(curso: dict, com: dict) -> dict:
        return comisiones.setdefault(
            (curso["course_id"], com["group_id"]),
            {
                "curso": curso["nombre"],
                "course_id": curso["course_id"],
                "comision": com["comision"].upper().replace("COM", "COM "),
                "group_id": com["group_id"],
                "participantes": None,
                "pendientes": 0,
                "donde": [],
                "fallaron": [],
                "actividades": 0,
                # El detalle por actividad. Es lo que ven las vistas de
                # Comisiones e Informes: la fila de arriba resume, esto muestra.
                "detalle": [],
            },
        )

    # Estas comisiones existen (tienen group_id real) pero no hay nada que consultar:
    # se crea la fila igual, degradada desde ya, con el motivo explícito — no es una
    # consulta que falló, es que "Mis datos" nunca guardó las tareas de este curso.
    for curso, com in sin_tareas:
        fila = _fila_base(curso, com)
        fila["fallaron"].append({
            "titulo": "(sin tareas en el snapshot)",
            "motivo": "El curso no tiene 'tareas' guardadas en \"Mis datos\" — corré "
                      "'Mis datos / remapear' (listar_tareas + guardar_mis_datos).",
        })

    for (curso, com, _), res in zip(trabajos, resultados):
        fila = _fila_base(curso, com)
        fila["actividades"] += 1

        if not res["ok"]:
            fila["fallaron"].append({"titulo": res["titulo"], "motivo": res["motivo"]})
            fila["detalle"].append(
                {
                    "unidad": _unidad(res["titulo"]),
                    "titulo": res["titulo"],
                    "assign_id": res["assign_id"],
                    # None, NO 0: no se pudo relevar y eso no es «nadie entregó».
                    "enviados": None,
                    "pendientes": None,
                    "motivo": res["motivo"],
                }
            )
            continue

        fila["detalle"].append(
            {
                "unidad": _unidad(res["titulo"]),
                "titulo": res["titulo"],
                "assign_id": res["assign_id"],
                "enviados": res["enviados"],
                "pendientes": res["pendientes"],
            }
        )

        # El padrón es el mismo para todas las actividades de la comisión; se
        # toma el primero que llegue con dato.
        if fila["participantes"] is None and res["participantes"] is not None:
            fila["participantes"] = res["participantes"]

        if res["pendientes"]:
            fila["pendientes"] += res["pendientes"]
            fila["donde"].append(
                {
                    "unidad": _unidad(res["titulo"]),
                    "titulo": res["titulo"],
                    "assign_id": res["assign_id"],
                    "pendientes": res["pendientes"],
                }
            )

    filas = list(comisiones.values())
    for f in filas:
        cuadre = cuadres.get((f["course_id"], f["group_id"])) or {}
        f["padron_cuadre"] = cuadre
        # Un alumno que las tareas no listan NO figura en ningún conteo de esta fila. El
        # número de participantes se lee como el padrón entero y no lo es.
        f["invisibles"] = cuadre.get("invisibles") or []
        f["degradado"] = bool(f["fallaron"]) or bool(f["invisibles"]) or (
            cuadre.get("verificado") is False)
        f["donde"].sort(key=lambda d: -d["pendientes"])

    total_fallas = sum(len(f["fallaron"]) for f in filas)

    return {
        "comisiones": filas,
        "procedencia": {
            "tool": "sumario",
            # + sin_tareas: cada comisión sin tareas cuenta como una consulta que no
            # se pudo ni intentar, no como una que simplemente no existió. Sin esto,
            # el aviso decía "4 de 0 consultas fallaron" — matemáticamente sin sentido.
            "consultas": len(trabajos) + len(sin_tareas),
            "fallaron": total_fallas,
            # `mis_datos` sale de un snapshot local que puede tener semanas: si
            # una comisión se agregó después, no está en esta foto y el panel
            # tiene que poder decirlo.
            "catalogo_at": crudo.get("actualizado_at"),
            "relevado_at": time.time(),
            "degradado": total_fallas > 0,
        },
    }


async def foto(refrescar: bool = False) -> dict:
    """
    La foto del día, con caché.

    El relevamiento son decenas de requests al campus: dispararlo en cada carga
    de pantalla es maltratar al servidor de la facultad y hacer esperar al tutor
    por un dato que no cambió.
    """
    fresca = _cache["foto"] is not None and (time.time() - _cache["at"]) < TTL_S
    if fresca and not refrescar:
        salida = dict(_cache["foto"])
        salida["procedencia"] = {**salida["procedencia"], "desde_cache": True}
        return salida

    nueva = await relevar()
    _cache["foto"] = nueva
    _cache["at"] = time.time()
    return {**nueva, "procedencia": {**nueva["procedencia"], "desde_cache": False}}
