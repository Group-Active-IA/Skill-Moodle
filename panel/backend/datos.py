"""
Los datos de Informes.

Importa `mcp/server.py` y llama sus tools **directamente**. No habla protocolo
MCP, no levanta un subproceso y no reimplementa nada: `@mcp.tool()` deja la
función original intacta, así que `server.sumario(...)` es una corrutina normal.

Eso importa más de lo que parece. Este proyecto tiene documentado lo que pasa
cuando algo rodea la skill para conseguir un dato que las tools no exponen: se
vuelve a descubrir de cero que las entregas vienen infladas con las que el alumno
abrió y nunca mandó, que Moodle guarda `-1` en la nota de lo que todavía no se
corrigió, que el `groupid` de una entrega viene 0 y no es "grupo 0". Llamando las
tools, el panel hereda cada uno de esos frenos y sus 139 tests.

**Acá no se calcula nada.** Si un número hace falta y ninguna tool lo da, se
expone en la tool, no se deriva en esta capa.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[2]
if str(SKILL_DIR / "mcp") not in sys.path:
    sys.path.insert(0, str(SKILL_DIR / "mcp"))

import server  # noqa: E402  (después de tocar sys.path)


async def mis_datos() -> dict:
    """Comisiones, cursos y actividades del tutor, según su config local."""
    return await server.mis_datos()


async def sumario(assign_id: str, group_id: int = 0) -> dict:
    """Conteo oficial de una actividad en una comisión. La fuente de verdad."""
    return await server.sumario(assign_id, group_id)


async def pendientes(assign_id: str, group_id: int = 0) -> dict:
    return await server.pendientes_por_corregir(assign_id, group_id)


async def sin_entrar(course_id: int, group_id: int = 0) -> dict:
    """Los dos relojes: campus y materia. Nunca uno solo."""
    return await server.sin_entrar_al_aula(course_id=course_id, group_id=group_id)


async def en_riesgo(course_id: int, group_id: int = 0) -> dict:
    return await server.alumnos_en_riesgo(course_id=course_id, group_id=group_id)


async def demora(course_id: int) -> dict:
    return await server.demora_correccion(course_id=course_id)


def procedencia(resultado: Any, tool: str) -> dict:
    """
    Envuelve una respuesta de tool con su procedencia, para que la pantalla
    pueda mostrar de dónde salió el número sin que cada vista lo arme a mano.

    El `_meta` de la skill (`fuente`, `degradado`, `avisos[]`) viaja tal cual:
    es lo que distingue "no hay nada" de "no se pudo relevar", que son opuestos
    y que en un tablero se leen igual si nadie los separa.
    """
    meta = {}
    if isinstance(resultado, dict):
        meta = resultado.get("_meta") or {}

    avisos = list(meta.get("avisos") or [])
    degradado = bool(meta.get("degradado"))

    return {
        "dato": resultado,
        "procedencia": {
            "tool": tool,
            "fuente": meta.get("fuente"),
            "degradado": degradado,
            "avisos": avisos,
            # `verificado` es False cuando la tool no pudo confirmar el dato.
            # La pantalla NUNCA muestra un vacío tranquilizador si esto es False.
            "verificado": not degradado and not avisos,
        },
    }
