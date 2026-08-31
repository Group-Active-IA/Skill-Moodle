"""
La ficha de una comisión: alumno por alumno.

Junta cuatro cosas que hoy hay que pedir por separado y cruzar a mano:

  - qué entregó cada uno, actividad por actividad (`entregas_tarea`)
  - los dos relojes de acceso (`sin_entrar_al_aula`)
  - sus últimos mensajes (`leer_mensajes` + `leer_conversacion`)
  - la nota, cuando ya está puesta

No calcula nada: junta. Y donde una fuente no contestó, la fila lo dice en vez
de rellenar con cero — un alumno con «0 entregas» porque falló la consulta y uno
con 0 entregas de verdad son personas distintas, y sólo a una hay que escribirle.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from . import datos
from .dia import CONCURRENCIA, _unidad, orden_actividad

# La ficha es cara (una consulta por actividad). Se guarda un rato, con la hora a
# la vista para que nadie confunda una foto vieja con el estado de ahora.
TTL_S = 300
_cache: dict[tuple[int, int], dict] = {}


async def _entregas_de(sem: asyncio.Semaphore, assign: dict, group_id: int) -> dict:
    async with sem:
        try:
            r = await datos.server.entregas_tarea(assign["assign_id"], group_id)
            return {"ok": True, "assign": assign, "datos": r}
        except Exception as exc:
            return {"ok": False, "assign": assign, "motivo": str(exc)[:160]}


# La bandeja del tutor tiene MUCHAS conversaciones (172 medidas el 2026-08-17,
# sobre 118 alumnos en cuatro comisiones). Pedir de menos no da un error: da una
# lista corta que se lee como "estos no hablaron nunca", y eso es exactamente el
# recorte silencioso que este proyecto persigue. Se pide con holgura y se declara
# si aun así tocó el techo.
TOPE_CONVERSACIONES = 500


async def _ultimos_mensajes(
    nombres: set[str], limite_por_alumno: int = 3
) -> tuple[dict[str, list[dict]], dict]:
    """
    Los últimos mensajes de los alumnos de ESTA comisión.

    `leer_mensajes` trae la bandeja entera con el último mensaje de cada hilo;
    para tener tres hay que abrir la conversación. Se abren **sólo las de los
    alumnos de la comisión**: abrir las 172 de la bandeja son 172 requests al
    campus para tirar el 80%.

    Devuelve también un informe de cobertura. Si la bandeja se truncó, quien
    muestre esto tiene que poder decir que la lista está incompleta en vez de
    dar a entender que nadie más escribió.
    """
    try:
        bandeja = await datos.server.leer_mensajes(limite=TOPE_CONVERSACIONES)
    except Exception as exc:
        return {}, {"ok": False, "motivo": str(exc)[:160]}

    todas = bandeja.get("conversaciones") or []
    convs = [c for c in todas if (c.get("alumno") or "").strip().upper() in nombres]
    cobertura = {
        "ok": True,
        "conversaciones_en_la_bandeja": len(todas),
        "de_esta_comision": len(convs),
        # Si la bandeja llegó justo al tope, puede haber más que no vinieron.
        "truncada": len(todas) >= TOPE_CONVERSACIONES,
    }
    sem = asyncio.Semaphore(CONCURRENCIA)

    async def traer(c: dict) -> tuple[str, list[dict]]:
        nombre = (c.get("alumno") or "").strip().upper()
        async with sem:
            try:
                hilo = await datos.server.leer_conversacion(
                    c["conversacion_id"], limite=limite_por_alumno
                )
            except Exception:
                # Se cae el hilo: queda el último mensaje, que ya lo teníamos.
                return nombre, [
                    {
                        "texto": c.get("ultimo_mensaje"),
                        "de_quien": c.get("de_quien"),
                        "timestamp": c.get("timestamp"),
                    }
                ]
        msgs = hilo.get("mensajes") or hilo.get("conversacion") or []
        salida = [
            {
                "texto": m.get("texto"),
                "de_quien": m.get("de_quien"),
                # `fecha_ts`, verificado en vivo. Adivinar el nombre del campo
                # no rompe nada ruidosamente: deja la fecha vacía y la columna
                # muestra un separador suelto que nadie sabe qué significa.
                "timestamp": m.get("fecha_ts"),
            }
            for m in msgs[-limite_por_alumno:]
        ]
        return nombre, salida or [
            {
                "texto": c.get("ultimo_mensaje"),
                "de_quien": c.get("de_quien"),
                "timestamp": c.get("timestamp"),
            }
        ]

    pares = await asyncio.gather(*(traer(c) for c in convs if c.get("conversacion_id")))
    return {n: m for n, m in pares if n}, cobertura


async def ficha(course_id: int, group_id: int, refrescar: bool = False) -> dict:
    clave = (course_id, group_id)
    guardada = _cache.get(clave)
    if guardada and not refrescar and (time.time() - guardada["at"]) < TTL_S:
        return {**guardada["ficha"], "desde_cache": True}

    crudo = await datos.mis_datos()
    cuerpo = crudo.get("datos", crudo)
    curso = next(
        (c for c in cuerpo.get("cursos", []) if c["course_id"] == course_id), None
    )
    if curso is None:
        return {"error": f"El curso {course_id} no está en tu configuración."}

    # El mismo orden que el mapa de Comisiones: las columnas de las dos vistas
    # tienen que significar lo mismo en la misma posición.
    tareas = sorted(curso.get("tareas", []), key=lambda t: orden_actividad(t["titulo"]))

    if not tareas:
        # Sin `tareas` en el snapshot no hay nada que pedir: la ficha saldría con
        # `alumnos: []` y `degradado: False` (nada falló, no hubo nada que consultar) —
        # indistinguible de una comisión real y vacía. Se corta acá con el aviso
        # explícito en vez de dejar que el silencio se lea como "al día".
        return {
            "course_id": course_id,
            "group_id": group_id,
            "curso": curso["nombre"],
            "alumnos": [],
            "actividades": [],
            "procedencia": {
                "tools": [],
                "consultas": 0,
                "fallaron": 0,
                "detalle_fallas": [],
                "relevado_at": time.time(),
                "mensajes": {"ok": False, "motivo": "no se llegó a pedir: sin tareas"},
                "degradado": True,
                "aviso": "El curso no tiene 'tareas' guardadas en \"Mis datos\" — corré "
                         "'Mis datos / remapear' (listar_tareas + guardar_mis_datos) "
                         "antes de confiar en esta ficha.",
            },
            "desde_cache": False,
        }

    sem = asyncio.Semaphore(CONCURRENCIA)

    entregas, accesos = await asyncio.gather(
        asyncio.gather(*(_entregas_de(sem, t, group_id) for t in tareas)),
        _accesos(course_id, group_id),
    )

    # Los mensajes van después: hacen falta los nombres de la comisión para no
    # abrir conversaciones de alumnos de otras.
    nombres = {
        a["nombre"].strip().upper()
        for res in entregas
        if res["ok"]
        for a in res["datos"].get("alumnos", [])
    }
    mensajes, cobertura_msg = await _ultimos_mensajes(nombres)

    # Un alumno por userid, con su fila de entregas.
    alumnos: dict[int, dict] = {}
    fallaron: list[dict] = []

    for res in entregas:
        assign = res["assign"]
        etiqueta = _unidad(assign["titulo"])
        if not res["ok"]:
            fallaron.append({"titulo": assign["titulo"], "motivo": res["motivo"]})
            continue
        for a in res["datos"].get("alumnos", []):
            fila = alumnos.setdefault(
                a["userid"],
                {
                    "userid": a["userid"],
                    "nombre": a["nombre"],
                    "entregas": [],
                    "entregadas": 0,
                    "sin_corregir": 0,
                },
            )
            fila["entregas"].append(
                {
                    "unidad": etiqueta,
                    "titulo": assign["titulo"],
                    "assign_id": assign["assign_id"],
                    "estado": a.get("estado"),
                    "nota": a.get("nota"),
                    "entregado": bool(a.get("entregado")),
                    "pendiente": bool(a.get("pendiente")),
                }
            )
            if a.get("entregado"):
                fila["entregadas"] += 1
            if a.get("pendiente"):
                fila["sin_corregir"] += 1

    for userid, acceso in accesos.items():
        if userid in alumnos:
            alumnos[userid].update(acceso)

    for fila in alumnos.values():
        fila["mensajes"] = mensajes.get(fila["nombre"].strip().upper(), [])

    orden = sorted(alumnos.values(), key=lambda a: a["nombre"])
    ficha_final = {
        "course_id": course_id,
        "group_id": group_id,
        "curso": curso["nombre"],
        "alumnos": orden,
        "actividades": [
            {"unidad": _unidad(t["titulo"]), "titulo": t["titulo"], "assign_id": t["assign_id"]}
            for t in tareas
        ],
        "procedencia": {
            "tools": ["entregas_tarea", "sin_entrar_al_aula", "leer_mensajes"],
            "consultas": len(tareas) + 2,
            "fallaron": len(fallaron),
            "detalle_fallas": fallaron,
            "relevado_at": time.time(),
            "mensajes": cobertura_msg,
            # Si alguna actividad no se pudo relevar, los contadores de entregas
            # de TODAS las filas están cortos. Se dice arriba, no en una nota al
            # pie: un conteo incompleto que parece completo es el error caro de
            # este proyecto.
            "degradado": bool(fallaron),
        },
    }

    _cache[clave] = {"ficha": ficha_final, "at": time.time()}
    return {**ficha_final, "desde_cache": False}


async def _accesos(course_id: int, group_id: int) -> dict[int, dict]:
    """Los dos relojes por alumno. Si la tool falla, se devuelve vacío y las
    filas quedan sin ese dato — visiblemente sin dato, no en cero."""
    try:
        r = await datos.sin_entrar(course_id, group_id)
    except Exception:
        return {}
    bloque = (r.get("comisiones") or [{}])[0]
    salida = {}
    for a in bloque.get("alumnos", []):
        salida[a["userid"]] = {
            "dias_sin_abrir_la_materia": a.get("dias_sin_abrir_la_materia"),
            "dias_sin_entrar_al_campus": a.get("dias_sin_entrar_al_campus"),
            "estado_aula": a.get("estado_aula"),
            "desenganchado": bool(a.get("desenganchado_de_la_materia")),
            "para_contactar": bool(a.get("entra_al_campus_sin_abrir_la_materia")),
        }
    return salida
