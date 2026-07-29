"""Informe PDF de correcciones pendientes, 100% sobre la API REST.

El copiloto armaba este PDF con `actions.py` (scraping Playwright). Acá se reescribe
contra `ws_api` (mod_assign_*): mismas columnas y layout, pero sin navegador y sin la
interfaz `MoodleClient` — recibe directamente el cliente REST (MobileWSClient)."""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import ws_api

NAVY = colors.HexColor("#1a3c5e")
LT = colors.HexColor("#eef3f7")


async def informe_pendientes(
    client, course_id: int, dest_dir: str,
    assign_ids: list[str] | None = None, group_id: int = 0,
) -> dict:
    """PDF con los alumnos pendientes de corrección por tarea (API REST).

    - `client`: MobileWSClient (token mobile).
    - `assign_ids`: si se pasa, limita a esas tareas (cmid); si no, todas las del curso.
    - `group_id`: 0 = todo el curso; >0 = esa comisión."""
    tareas = await ws_api.listar_tareas(client, course_id)
    by_id = {t["id"]: t["titulo"] for t in tareas}
    ids = assign_ids or [t["id"] for t in tareas]

    secciones = []
    total = 0
    # Tareas que no se pudieron relevar. Antes se hacía `continue` a secas: la tarea
    # desaparecía del PDF y el retorno decía ok=True igual, así que un informe incompleto
    # era indistinguible de uno completo. Con varios tutores pegándole al mismo campus un
    # timeout suelto es cuestión de tiempo, y "esta tarea no tiene pendientes" no es lo
    # mismo que "no pude consultarla".
    no_relevadas = []
    for aid in ids:
        data = await ws_api.pendientes_tarea(client, aid, group_id)
        if data.get("error"):
            no_relevadas.append({"assign_id": aid, "tarea": by_id.get(aid, aid),
                                 "motivo": data["error"]})
            continue
        if data["pendientes"] > 0:
            secciones.append((by_id.get(aid, aid), data["alumnos"]))
            total += data["pendientes"]

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"informe_pendientes_curso{course_id}.pdf")
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=16, textColor=NAVY)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor("#1c5a7a"))
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8, textColor=colors.grey)
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    E = [Paragraph("Informe de correcciones pendientes", h1),
         Paragraph(f"Curso {course_id} · Total pendientes: {total}", small), Spacer(1, 10)]
    if no_relevadas:
        # El aviso va en el PDF además de en la respuesta: el PDF se comparte y se imprime
        # solo, sin el dict que lo acompañó.
        E.append(Paragraph(
            f"⚠️ {len(no_relevadas)} tarea(s) NO se pudieron consultar y quedaron FUERA de "
            "este informe: " + ", ".join(str(t["tarea"])[:40] for t in no_relevadas[:6])
            + ". Los totales de arriba están incompletos.", small))
        E.append(Spacer(1, 10))
    for titulo, alumnos in secciones:
        E.append(Paragraph(f"{titulo} — {len(alumnos)} pendiente(s)", h2))
        rows = [["Alumno", "Grupo", "Email"]] + [
            [a["name"], a.get("grupo") or "—", a["email"]] for a in alumnos
        ]
        t = Table(rows, colWidths=[6 * cm, 3 * cm, 7 * cm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LT])]))
        E.append(t)
        E.append(Spacer(1, 8))
    doc.build(E)
    salida = {
        "ok": True, "archivo": path, "total_pendientes": total,
        "tareas_relevadas": len(ids) - len(no_relevadas), "tareas_pedidas": len(ids),
        "_meta": {"fuente": "vivo", "degradado": bool(no_relevadas),
                  "tareas_no_relevadas": no_relevadas},
    }
    if no_relevadas:
        salida["aviso"] = (
            f"⚠️ {len(no_relevadas)} de {len(ids)} tarea(s) no se pudieron consultar y NO "
            f"están en el informe: el total de {total} pendientes está incompleto. "
            "Mirá `tareas_no_relevadas` y volvé a generarlo.")
    return salida
