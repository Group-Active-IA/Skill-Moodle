"""
El panel local del tutor.

Un solo proceso: sirve la interfaz ya compilada y expone la API. Escucha en
127.0.0.1 y sólo ahí — corre con las credenciales de Moodle del tutor y puede
escribir en el campus, así que exponerlo a la red no sería una comodidad, sería
un agujero.

Arrancar:
    .venv/bin/python -m panel.backend.app
o bien:
    .venv/bin/uvicorn panel.backend.app:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agente, comision, datos, dia

PANEL_DIR = Path(__file__).resolve().parent.parent
WEB_DIST = PANEL_DIR / "web" / "dist"

app = FastAPI(title="Campus Navigator · Panel", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #


class Mensaje(BaseModel):
    texto: str
    sesion: str | None = None


class Decision(BaseModel):
    id: str
    ok: bool
    entrada: dict | None = None
    motivo: str | None = None


@app.post("/api/chat")
async def chat(msg: Mensaje):
    """
    Manda un turno y devuelve la respuesta como SSE.

    El stream puede quedar esperando una confirmación: cuando llega un evento
    `confirmacion`, el turno está frenado hasta que el navegador conteste por
    `/api/chat/{sid}/confirmar`, que es otro request y corre en paralelo.
    """
    if msg.sesion and msg.sesion in agente.SESIONES:
        sesion = agente.SESIONES[msg.sesion]
    else:
        sesion = await agente.abrir_sesion()

    async def stream():
        yield agente.sse({"tipo": "sesion", "id": sesion.id})
        async for evento in agente.conversar(sesion, msg.texto):
            yield agente.sse(evento)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/{sid}/confirmar")
async def confirmar(sid: str, decision: Decision):
    ok = agente.responder_confirmacion(
        sid,
        decision.id,
        {"ok": decision.ok, "entrada": decision.entrada, "motivo": decision.motivo},
    )
    if not ok:
        # Que no haya nadie esperando es un estado real, no un error del tutor:
        # pasa si recargó la página con una confirmación abierta.
        raise HTTPException(status_code=409, detail="Esa confirmación ya no está esperando.")
    return {"ok": True}


@app.delete("/api/chat/{sid}")
async def cerrar(sid: str):
    await agente.cerrar_sesion(sid)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Informes
# --------------------------------------------------------------------------- #


@app.get("/api/dia")
async def el_dia(refrescar: bool = False):
    """Lo que hay en cada comisión propia. Es lo primero que ve el tutor."""
    return await dia.foto(refrescar=refrescar)


@app.get("/api/comision")
async def ficha_comision(course_id: int, group_id: int, refrescar: bool = False):
    """Alumno por alumno: entregas, los dos relojes de acceso y últimos mensajes."""
    return await comision.ficha(course_id, group_id, refrescar=refrescar)


@app.get("/api/inactivos")
async def inactivos(course_id: int, group_id: int = 0):
    """
    Quién dejó de abrir ESTA materia. Los dos relojes, nunca uno solo: el que
    entra al campus todos los días para otra materia y hace un mes que no abre
    la propia figura «al día» si se mira el reloj equivocado.
    """
    return datos.procedencia(
        await datos.sin_entrar(course_id, group_id), "sin_entrar_al_aula"
    )


@app.get("/api/mis-datos")
async def mis_datos():
    return datos.procedencia(await datos.mis_datos(), "mis_datos")


@app.get("/api/sumario")
async def sumario(assign_id: str, group_id: int = 0):
    return datos.procedencia(await datos.sumario(assign_id, group_id), "sumario")


@app.get("/api/pendientes")
async def pendientes(assign_id: str, group_id: int = 0):
    return datos.procedencia(
        await datos.pendientes(assign_id, group_id), "pendientes_por_corregir"
    )


@app.get("/api/sin-entrar")
async def sin_entrar(course_id: int, group_id: int = 0):
    return datos.procedencia(
        await datos.sin_entrar(course_id, group_id), "sin_entrar_al_aula"
    )


@app.get("/api/salud")
async def salud():
    version = (PANEL_DIR.parent / "VERSION").read_text().strip()
    return {"ok": True, "version_skill": version}


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{ruta:path}")
    async def spa(ruta: str):
        archivo = WEB_DIST / ruta
        if ruta and archivo.is_file():
            return FileResponse(archivo)
        return FileResponse(WEB_DIST / "index.html")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")


if __name__ == "__main__":
    main()
