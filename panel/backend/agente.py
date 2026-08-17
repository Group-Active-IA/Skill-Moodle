"""
El chat del panel.

Levanta una sesión del Agent SDK con la skill y el MCP `moodle-tutor` conectados,
así que conversar acá es lo mismo que conversar en la terminal: mismas 44 tools,
mismas reglas, mismo criterio.

Dos cosas importan más que el resto:

1. **No hace falta API key.** El SDK lanza el CLI de Claude Code del propio tutor
   y hereda su sesión. Verificado en vivo con el entorno sin `ANTHROPIC_API_KEY`.

2. **El gate de escritura lo aplica el harness, no el prompt.** `can_use_tool` se
   ejecuta antes de cada tool: si la tool escribe en el campus, la corrida se
   frena y espera un OK explícito del tutor. El modelo no puede saltearlo aunque
   quiera, que es la diferencia con pedírselo por prompt.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

SKILL_DIR = Path(__file__).resolve().parents[2]
SERVER_PY = SKILL_DIR / "mcp" / "server.py"
PYTHON = SKILL_DIR / ".venv" / "bin" / "python"

# Configuración propia del tutor. Vive en su carpeta personal y NO en el repo:
# es un repo compartido por ~25 personas y las rutas de uno no son las de otro.
CONFIG_LOCAL = Path.home() / ".moodle-skill" / "panel.json"


def _carpetas_extra() -> list[Path]:
    """
    Carpetas que el tutor decidió abrirle al panel, además de la skill.

    Sirve para el caso de quien ya tiene su trabajo docente organizado afuera
    —fichas de comisión, apuntes, un vault de notas— y quiere que el agente del
    panel lo lea igual que lo lee en su terminal.

    Por omisión NO hay ninguna: quien no configure nada tiene un panel que sólo
    ve la skill, que es lo que corresponde a un producto compartido.

    Formato de `~/.moodle-skill/panel.json`:

        {"carpetas": ["/home/tutor/Proyectos/Tutor-TUPAD"]}

    Las rutas que no existen se ignoran en silencio: una carpeta que se movió no
    tiene que impedir que el panel arranque.
    """
    if not CONFIG_LOCAL.is_file():
        return []
    try:
        cfg = json.loads(CONFIG_LOCAL.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    rutas = []
    for c in cfg.get("carpetas") or []:
        p = Path(str(c)).expanduser()
        if p.is_dir():
            rutas.append(p.resolve())
    return rutas


CARPETAS_EXTRA = _carpetas_extra()

# Tope por archivo. Un CLAUDE.md gigante no puede comerse el contexto del panel.
TOPE_INSTRUCCIONES = 20_000


def _instrucciones_extra() -> str:
    """
    El `CLAUDE.md` de cada carpeta abierta, metido en el prompt del sistema.

    Hace falta hacerlo a mano: abrirle una carpeta al agente le da acceso a los
    archivos, pero **no carga su `CLAUDE.md`** — sólo se cargan el del directorio
    primario y sus padres. Verificado en vivo preguntándoselo al propio panel,
    que contestó que no lo tenía.

    Importa más de lo que parece. Esos archivos son los que dicen *cómo* se
    mantiene cada cosa: qué sección de una nota se reescribe y cuál se apila, qué
    no se duplica, dónde no hay que escribir. Sin ellos el agente tiene la llave
    de la carpeta y ninguna de las reglas de la casa, que es la peor de las dos
    combinaciones.
    """
    partes: list[str] = []
    for carpeta in CARPETAS_EXTRA:
        for nombre in ("CLAUDE.md", "AGENTS.md"):
            archivo = carpeta / nombre
            if not archivo.is_file():
                continue
            try:
                texto = archivo.read_text(encoding="utf-8")[:TOPE_INSTRUCCIONES]
            except OSError:
                continue
            partes.append(
                f"<instrucciones-de-carpeta ruta=\"{archivo}\">\n"
                f"{texto}\n"
                f"</instrucciones-de-carpeta>"
            )

    if not partes:
        return ""

    encabezado = (
        "El tutor abrió estas carpetas al panel desde su configuración local, y "
        "con ellas sus instrucciones. Valen igual que las del proyecto para todo "
        "lo que se haga adentro de esas rutas: cómo se mantiene cada archivo, qué "
        "se reescribe, qué se apila y dónde no hay que escribir.\n\n"
        "Si dos instrucciones se contradicen, gana la más específica a la carpeta "
        "donde vas a escribir, y se dice cuál se aplicó en vez de elegir en "
        "silencio."
    )
    return encabezado + "\n\n" + "\n\n".join(partes)


INSTRUCCIONES_EXTRA = _instrucciones_extra()

# Dónde puede escribir archivos el agente: la skill y lo que el tutor abrió a
# mano. Nada más.
ESCRITURA_PERMITIDA = [SKILL_DIR.resolve(), *CARPETAS_EXTRA]

# Tools que escriben archivos en el disco. Se gatean por RUTA, no por nombre:
# el punto no es que no escriba, es que no escriba afuera de lo declarado.
TOOLS_DE_ARCHIVO = {"Write", "Edit", "NotebookEdit", "MultiEdit"}


def _dentro_de_lo_permitido(ruta: str) -> bool:
    try:
        destino = Path(ruta).expanduser().resolve()
    except (OSError, ValueError):
        return False
    return any(
        destino == base or base in destino.parents for base in ESCRITURA_PERMITIDA
    )

# Las tools que ESCRIBEN en el campus. Toda escritura por acá es irreversible
# desde la API, así que ninguna corre sin un OK explícito del tutor.
#
# La lista es una lista blanca invertida a propósito: lo que no está acá, corre
# solo. Si mañana se agrega una tool de escritura y nadie toca este archivo, la
# escritura pasaría sin gate. Por eso `_es_escritura` además frena cualquier tool
# desconocida que reciba un parámetro `confirmado`, que es como la skill marca
# sus propias escrituras.
TOOLS_QUE_ESCRIBEN = {
    "cargar_nota",
    "confirmar_cola",
    "responder_mensaje",
    "responder_foro",
    "crear_discusion",
    "corregir_con_active_ia",
}

# Lo que la skill toca fuera del campus pero igual no debería pasar callado.
TOOLS_SENSIBLES = {"configurar", "actualizar_skill", "guardar_mis_datos"}


def _nombre_corto(tool: str) -> str:
    """`mcp__moodle-tutor__cargar_nota` -> `cargar_nota`."""
    return tool.rsplit("__", 1)[-1]


def _es_escritura(tool: str, entrada: dict[str, Any]) -> bool:
    corto = _nombre_corto(tool)
    if corto in TOOLS_QUE_ESCRIBEN or corto in TOOLS_SENSIBLES:
        return True
    # Red de seguridad: la skill marca sus escrituras con `confirmado`.
    # Una tool nueva que lo use queda frenada sin tener que tocar la lista.
    return "confirmado" in entrada


@dataclass
class Sesion:
    """Una conversación. Vive mientras el panel esté abierto."""

    id: str
    client: ClaudeSDKClient
    # Confirmaciones esperando respuesta del tutor, por `tool_use_id`.
    pendientes: dict[str, asyncio.Future] = field(default_factory=dict)
    # Cola por donde salen los eventos hacia el navegador.
    eventos: asyncio.Queue = field(default_factory=asyncio.Queue)


SESIONES: dict[str, Sesion] = {}


def _opciones(sesion_holder: dict[str, Sesion | None]) -> ClaudeAgentOptions:
    """
    `sesion_holder` es una caja de un elemento: el hook `can_use_tool` necesita
    la sesión, pero la sesión necesita el cliente, que necesita las opciones.
    Se rompe el círculo pasando la caja y llenándola después.
    """

    async def puede_usar(tool: str, entrada: dict[str, Any], contexto: Any):
        sesion = sesion_holder.get("s")

        # Escritura de archivos: se permite adentro de lo declarado y se niega
        # afuera. El tutor abrió su carpeta de trabajo a propósito y no tiene
        # sentido preguntarle por cada línea que edita ahí; lo que no puede
        # pasar es que el panel escriba en cualquier parte del disco.
        if tool in TOOLS_DE_ARCHIVO:
            destino = entrada.get("file_path") or entrada.get("notebook_path") or ""
            if destino and not _dentro_de_lo_permitido(str(destino)):
                permitidas = ", ".join(str(p) for p in ESCRITURA_PERMITIDA)
                return PermissionResultDeny(
                    message=(
                        f"El panel no escribe fuera de sus carpetas. Permitidas: "
                        f"{permitidas}. Para sumar una, agregala a "
                        f"{CONFIG_LOCAL}."
                    ),
                    interrupt=False,
                )
            return PermissionResultAllow()

        if sesion is None or not _es_escritura(tool, entrada):
            return PermissionResultAllow()

        tool_use_id = getattr(contexto, "tool_use_id", None) or str(uuid.uuid4())
        futuro: asyncio.Future = asyncio.get_running_loop().create_future()
        sesion.pendientes[tool_use_id] = futuro

        await sesion.eventos.put(
            {
                "tipo": "confirmacion",
                "id": tool_use_id,
                "tool": _nombre_corto(tool),
                "irreversible": _nombre_corto(tool) in TOOLS_QUE_ESCRIBEN,
                "entrada": entrada,
            }
        )

        try:
            decision = await futuro
        finally:
            sesion.pendientes.pop(tool_use_id, None)

        if decision.get("ok"):
            # `updated_input` deja que el tutor corrija la nota o el texto en la
            # misma pantalla de confirmación, sin volver a pedírselo al modelo.
            cambios = decision.get("entrada")
            if cambios and cambios != entrada:
                return PermissionResultAllow(updated_input=cambios)
            return PermissionResultAllow()

        return PermissionResultDeny(
            message=decision.get("motivo") or "El tutor no confirmó la operación.",
            interrupt=False,
        )

    return ClaudeAgentOptions(
        cwd=str(SKILL_DIR),
        # Las carpetas que el tutor abrió en su config local. Vacío por omisión.
        add_dirs=[str(p) for p in CARPETAS_EXTRA],
        # Carga la skill instalada del tutor: SKILL.md es el manual que lee el
        # modelo, y sin él las tools existen pero nadie sabe cuándo usarlas.
        setting_sources=["user", "project"],
        skills=["tup-campus-navigator"],
        mcp_servers={
            "moodle-tutor": {
                "type": "stdio",
                "command": str(PYTHON) if PYTHON.exists() else "python3",
                "args": [str(SERVER_PY)],
            }
        },
        # SÓLO el MCP de arriba. Sin esto, la sesión hereda todos los servidores
        # que el tutor tenga enchufados en su Claude Code — verificado en vivo:
        # aparecieron nueve, entre ellos Notion, Linear, ClickUp y Drive.
        #
        # No es superficie de más y nada más: el gate de escritura de este panel
        # frena las tools de Moodle, no las de Notion. Una tool de otro servidor
        # escribiría sin que nadie la detenga, en un lugar que este producto ni
        # siquiera sabe que existe. Y peor para un repo compartido: cada tutor
        # tendría un panel distinto según lo que tenga instalado.
        strict_mcp_config=True,
        permission_mode="default",
        can_use_tool=puede_usar,
        include_partial_messages=True,
        system_prompt=(
            {
                "type": "preset",
                "preset": "claude_code",
                "append": INSTRUCCIONES_EXTRA,
            }
            if INSTRUCCIONES_EXTRA
            else {"type": "preset", "preset": "claude_code"}
        ),
    )


async def abrir_sesion() -> Sesion:
    holder: dict[str, Sesion | None] = {"s": None}
    client = ClaudeSDKClient(options=_opciones(holder))
    await client.connect()
    sesion = Sesion(id=str(uuid.uuid4()), client=client)
    holder["s"] = sesion
    SESIONES[sesion.id] = sesion
    return sesion


async def cerrar_sesion(sid: str) -> None:
    sesion = SESIONES.pop(sid, None)
    if sesion is not None:
        await sesion.client.disconnect()


def responder_confirmacion(sid: str, tool_use_id: str, decision: dict) -> bool:
    """Devuelve False si nadie estaba esperando esa confirmación."""
    sesion = SESIONES.get(sid)
    if sesion is None:
        return False
    futuro = sesion.pendientes.get(tool_use_id)
    if futuro is None or futuro.done():
        return False
    futuro.set_result(decision)
    return True


def _texto_parcial(msg: Any) -> str | None:
    """Extrae el delta de texto de un evento de streaming, si lo trae."""
    evento = getattr(msg, "event", None)
    if not isinstance(evento, dict):
        return None
    if evento.get("type") != "content_block_delta":
        return None
    delta = evento.get("delta") or {}
    if delta.get("type") == "text_delta":
        return delta.get("text")
    return None


async def conversar(sesion: Sesion, prompt: str):
    """
    Manda el turno y va emitiendo eventos a medida que llegan.

    Cada evento sale como un dict listo para serializar. El frontend no
    interpreta nada del protocolo del SDK: sólo estos tipos.
    """
    await sesion.client.query(prompt)

    async def bombear():
        try:
            async for msg in sesion.client.receive_response():
                delta = _texto_parcial(msg)
                if delta:
                    await sesion.eventos.put({"tipo": "texto", "delta": delta})
                    continue

                if isinstance(msg, AssistantMessage):
                    for bloque in msg.content:
                        if isinstance(bloque, ToolUseBlock):
                            await sesion.eventos.put(
                                {
                                    "tipo": "herramienta",
                                    "tool": _nombre_corto(bloque.name),
                                    # `propia` distingue una consulta al campus de
                                    # la maquinaria del harness (leer un archivo,
                                    # buscar una tool). Al tutor le importa la
                                    # primera; la segunda es ruido de implementación
                                    # y la pantalla la trata distinto.
                                    "propia": bloque.name.startswith("mcp__moodle-tutor__"),
                                    "entrada": bloque.input,
                                }
                            )
                        elif isinstance(bloque, ThinkingBlock):
                            await sesion.eventos.put({"tipo": "pensando"})
                elif isinstance(msg, ResultMessage):
                    await sesion.eventos.put({"tipo": "fin"})
                    return
        except Exception as exc:  # el stream muere: se dice, no se traga
            await sesion.eventos.put({"tipo": "error", "mensaje": str(exc)})
            await sesion.eventos.put({"tipo": "fin"})

    tarea = asyncio.create_task(bombear())

    try:
        while True:
            evento = await sesion.eventos.get()
            yield evento
            if evento.get("tipo") == "fin":
                return
    finally:
        if not tarea.done():
            tarea.cancel()


def sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"
