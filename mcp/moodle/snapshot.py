"""Snapshot del estado de corrección de los cursos del tutor (versión de-un-tutor).

Recorre las comisiones del tutor y las tareas relevantes (TPs, parciales y el TIO),
calcula por (comisión, tarea) cuántos alumnos hay / entregaron / están calificados /
faltan corregir, y lo persiste LOCAL con `almacen`. Refresca además la caché de alumnos
(nombre, comisión, último acceso REAL) que alimenta `buscar_alumno`.

Todo por la **API REST oficial de Moodle** (mod_assign_list_participants +
mod_assign_get_grades) — sin scraping, sin navegador.

Diferencias con el copiloto (a propósito):
- NO hay `db` multi-tenant ni `settings`: la config sale del `mis_datos.json` local y la
  persistencia es un SQLite local (ver `almacen`).
- NO hay `tutor_id` ni SessionPool: un solo cliente (MobileWSClient de env vars).
- Se MANTIENE la lección clave: iterar TODOS los cursos que el tutor mapeó, no un
  course_id fijo — un tutor de Prog II/III no juntaría nada con un curso hardcodeado.

Desde `mcp/`:  python -m moodle.snapshot
"""

import asyncio
import logging
import re
from datetime import date, datetime

from . import almacen, ws_api

log = logging.getLogger("skill.snapshot")


async def _comisiones_del_curso(course_id: int) -> list[dict]:
    """Comisiones del tutor para un curso, desde SU `mis_datos.json`. Sin fallback
    hardcodeado (eso era del tutor seed del copiloto): si no mapeó el curso, devuelve []."""
    datos = await almacen.get_mis_datos()
    if not datos:
        return []
    for curso in datos.get("cursos", []):
        if curso.get("course_id") == course_id:
            return [
                {"group_id": c["group_id"], "comision": c["comision"]}
                for c in curso.get("comisiones_del_tutor", [])
                if c.get("group_id") and c.get("comision")
            ]
    return []


def _es_tarea_relevante(titulo: str) -> bool:
    """TPs ('Actividad de cierre unidad N'), parciales y el integrador (TIO)."""
    t = (titulo or "").lower()
    if "parcial" in t or "integrador" in t or re.search(r"\btio\b", t):
        return True
    if "actividad de cierre" in t:
        return True
    return bool(re.search(r"\btp\s*0?\d{1,2}\b", t))


def _contar(rows: list[dict]) -> dict:
    return {
        "participantes": len(rows),
        "entregados": sum(1 for r in rows if r["entregado"]),
        "calificados": sum(1 for r in rows if r["calificado"]),
        "pendientes": sum(1 for r in rows if r["pendiente"]),
    }


def _fmt_acceso(ts: int | None) -> str:
    """Último acceso (unix ts de la API) a texto legible."""
    if not ts:
        return "Nunca"
    dias = (int(datetime.now().timestamp()) - int(ts)) // 86400
    if dias <= 0:
        return "Hoy"
    if dias == 1:
        return "hace 1 día"
    return f"hace {dias} días"


async def _cursos_a_relevar(course_ids: list[int] | None) -> list[int]:
    """course_ids a relevar. Si el caller no los pasa, se leen TODOS los que el tutor
    mapeó en `mis_datos.json` (la lección de hoy: no un course_id fijo)."""
    if course_ids:
        return [int(c) for c in course_ids]
    datos = await almacen.get_mis_datos()
    ids: list[int] = []
    if datos:
        for curso in datos.get("cursos", []):
            cid = curso.get("course_id")
            if cid is not None:
                ids.append(int(cid))
    return ids


async def tomar_snapshot(client, course_ids: list[int] | None = None) -> dict:
    """Releva (comisión, tarea) -> conteos y refresca el padrón, para el tutor, por API
    REST. Recorre TODOS los cursos pasados en `course_ids`; si es None, TODOS los que el
    tutor mapeó en `mis_datos.json` (nunca un course_id hardcodeado). Persiste local."""
    hoy = date.today().isoformat()
    cursos = await _cursos_a_relevar(course_ids)
    if not cursos:
        log.info("Sin cursos para relevar (pasá course_ids o mapeá 'mis_datos') — snapshot omitido")
        return {"fecha": hoy, "filas": 0, "entregas": 0, "alumnos": 0, "omitido": True}

    amap = await ws_api._assign_map(client)  # cmid -> {id, course, grade, name} (todos los cursos)
    filas = 0
    entregas_acc: list[dict] = []
    padron: dict[str, dict] = {}  # email -> {nombre, comisiones:set, ultimo_acceso:ts}
    grades_cache: dict[int, dict] = {}
    total_comisiones = 0

    for course_id in cursos:
        comisiones = await _comisiones_del_curso(course_id)
        if not comisiones:
            continue
        total_comisiones += len(comisiones)
        tareas = [
            {"id": cmid, "titulo": cfg.get("name", ""), "grade": cfg.get("grade"), "inst": cfg.get("id")}
            for cmid, cfg in amap.items()
            if cfg.get("course") == course_id and cfg.get("id") and _es_tarea_relevante(cfg.get("name", ""))
        ]
        log.info("Snapshot %s — curso %s — %d comisiones, %d tareas (API REST)",
                 hoy, course_id, len(comisiones), len(tareas))

        for com in comisiones:
            for t in tareas:
                lp = None
                for intento in range(3):
                    try:
                        lp = await client.ws("mod_assign_list_participants", {
                            "assignid": t["inst"], "groupid": com["group_id"],
                            "filter": "", "skip": 0, "limit": 0})
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("  %-30s intento %d falló (%s)",
                                    t["titulo"][:30], intento + 1, type(e).__name__)
                if lp is None:
                    prev = await almacen.entregas_previas(com["comision"], t["id"])
                    entregas_acc.extend(prev)
                    log.warning("  %-30s SALTEADA — reuso %d entregas previas", t["titulo"][:30], len(prev))
                    continue

                est = [p for p in lp if ws_api._es_estudiante(p)]
                if t["inst"] not in grades_cache:
                    grades_cache[t["inst"]] = await ws_api.grades_map(client, t["inst"])
                gmap = grades_cache[t["inst"]]

                rows = []
                for p in est:
                    submitted = bool(p.get("submitted"))
                    requiere = bool(p.get("requiregrading"))
                    nota = ws_api.nota_display(t["grade"], gmap.get(p["id"]))
                    entregado = submitted
                    calificado = submitted and not requiere and nota is not None
                    pendiente = submitted and requiere
                    email = (p.get("email") or "").lower()
                    rows.append({"name": p.get("fullname"), "email": email,
                                 "entregado": entregado, "calificado": calificado, "pendiente": pendiente})
                    entregas_acc.append({
                        "email": email, "comision": com["comision"],
                        "assign_id": t["id"], "tarea": t["titulo"],
                        "estado": ("Enviado para calificar" if pendiente else
                                   "Calificado" if calificado else
                                   "Enviado" if submitted else "Sin entrega"),
                        "nota": nota, "pendiente": pendiente,
                    })
                    reg = padron.setdefault(email, {
                        "nombre": p.get("fullname"), "comisiones": set(), "ultimo_acceso": 0})
                    reg["comisiones"].add(com["comision"])
                    reg["ultimo_acceso"] = max(reg["ultimo_acceso"], p.get("lastaccess") or 0)

                c = _contar(rows)
                await almacen.guardar_snapshot({
                    "fecha": hoy, "comision": com["comision"],
                    "assign_id": t["id"], "tarea": t["titulo"], **c,
                    "datos_json": {
                        "group_id": com["group_id"],
                        "no_entregaron": [
                            {"name": r["name"], "email": r["email"]}
                            for r in rows if not r["entregado"]
                        ],
                    },
                })
                filas += 1
                log.info("  %-30s part=%2d entreg=%2d calif=%2d pend=%2d",
                         t["titulo"][:30], c["participantes"], c["entregados"],
                         c["calificados"], c["pendientes"])

    if total_comisiones == 0:
        log.info("Sin comisiones en los cursos mapeados — snapshot omitido")
        return {"fecha": hoy, "filas": 0, "entregas": 0, "alumnos": 0, "omitido": True}

    entregas = await almacen.reemplazar_entregas(entregas_acc)
    alumnos = 0
    for email, reg in padron.items():
        await almacen.upsert_alumno(
            email=email, nombre=reg["nombre"],
            comision=", ".join(sorted(reg["comisiones"])) or None,
            ultimo_acceso=_fmt_acceso(reg["ultimo_acceso"]),
        )
        alumnos += 1
    log.info("Snapshot OK: %d filas, %d entregas, %d alumnos", filas, entregas, alumnos)
    return {"fecha": hoy, "filas": filas, "entregas": entregas, "alumnos": alumnos}


async def _main() -> None:
    """CLI: corre el snapshot del tutor con las credenciales de env vars.
    Útil para poblar la caché desde cron sin levantar el MCP server."""
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from .cliente import MobileWSClient

    base = os.environ.get("MOODLE_URL", "https://tup.sied.utn.edu.ar")
    usuario = os.environ.get("MOODLE_USER")
    password = os.environ.get("MOODLE_PASS")
    if not usuario or not password:
        raise SystemExit("Faltan MOODLE_USER / MOODLE_PASS en el entorno.")

    await almacen.init_db()
    cli = MobileWSClient(base, usuario, password)
    res = await tomar_snapshot(cli)
    log.info("Resultado: %s", res)


if __name__ == "__main__":
    asyncio.run(_main())
