"""Informes PDF de la skill, 100% sobre la API REST.

Dos documentos distintos y con destinatarios distintos:

- `informe_pendientes` — para el TUTOR: qué le falta corregir en su comisión. El copiloto lo
  armaba con `actions.py` (scraping Playwright); acá se reescribe contra `ws_api`
  (mod_assign_*): mismas columnas y layout, pero sin navegador.
- `informe_profesor_pdf` — para el PROFESOR / coordinador: el curso entero. Es una función
  PURA de renderizado (recibe el dict de `panorama.informe_profesor`, no el cliente), así que
  se puede testear sin red y sin credenciales — que es donde este proyecto encuentra los bugs.
"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import ws_api

NAVY = colors.HexColor("#1a3c5e")
LT = colors.HexColor("#eef3f7")
TEAL = colors.HexColor("#1f7a6c")
AMBER = colors.HexColor("#b07d1a")
RED = colors.HexColor("#a02c2c")
GREY = colors.HexColor("#dddddd")


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


# ---------------------------------------------------------------------------
# PDF del PROFESOR — render PURO del dict de `panorama.informe_profesor`
# ---------------------------------------------------------------------------

def _tiles(items: list[tuple], ancho_total: float = 17.0) -> Table:
    """Fila de KPIs. `items`: (valor, etiqueta, color)."""
    n = max(len(items), 1)
    est_v = ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=17, leading=19,
                           alignment=1, textColor=colors.white)
    est_e = ParagraphStyle("ke", fontName="Helvetica", fontSize=6.5, leading=8,
                           alignment=1, textColor=colors.white)
    fila = [[Paragraph(str(v), est_v), Paragraph(e, est_e)] for v, e, _ in items]
    celdas = [[Table([[c[0]], [c[1]]], style=TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])) for c in fila]]
    t = Table(celdas, colWidths=[ancho_total / n * cm] * n, rowHeights=[2.0 * cm])
    est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for i, (_, _, color) in enumerate(items):
        est.append(("BACKGROUND", (i, 0), (i, 0), color))
    t.setStyle(TableStyle(est))
    return t


def _tabla(rows: list[list], anchos: list[float], font: float = 7.2,
           alinear_der: list[int] | None = None) -> Table:
    t = Table(rows, colWidths=[a * cm for a in anchos], repeatRows=1)
    est = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
           ("FONTSIZE", (0, 0), (-1, -1), font),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("GRID", (0, 0), (-1, -1), 0.3, GREY),
           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LT]),
           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    for c in (alinear_der or []):
        est.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(est))
    return t


def _dias_txt(d) -> str:
    return "—" if d is None else (f"{d:g}" if isinstance(d, float) else str(d))


def puntos_de_atencion(datos: dict, tope: int = 4) -> list[tuple]:
    """PURA: los focos del curso, en orden, calculados de los datos. -> [(titulo, detalle)].

    Es el bloque que en el informe original decía "Puntos de atención de hoy" y venía escrito
    por un modelo a partir de los números. Acá se calcula, y por eso **no** hay adjetivos ni un
    "estado general": cada punto es un hecho con su cuenta al lado y dice a quién llamar.

    El orden no es un ranking de nada: primero lo más recuperable (el que está en el campus y no
    abre la materia), después lo que ya se enfrió, y al final el trabajo de corrección — que en
    este campus casi siempre está al día y por eso no es la noticia.
    """
    h, des = datos.get("hechos", {}), datos.get("desenganche", {})
    tot = des.get("totales", {})
    por_com = des.get("por_comision", {})
    puntos: list[tuple] = []

    activos = tot.get("entran_al_campus_sin_abrir_la_materia") or 0
    if activos:
        # De dónde salen: las comisiones que más concentran, como dato de ruteo.
        top = sorted(((c, v.get("entran_al_campus_sin_abrir_la_materia") or 0)
                      for c, v in por_com.items() if not v.get("sin_medir")),
                     key=lambda kv: -kv[1])[:3]
        donde = ", ".join(f"{c} ({n})" for c, n in top if n)
        puntos.append((
            f"{activos} alumnos entran al campus y NO abren la materia",
            f"Están usando el campus para otra cosa: no perdieron el acceso, eligieron no "
            f"entrar acá. Es el grupo más recuperable y el que un corte por «días sin entrar al "
            f"campus» no encuentra. Donde más se concentra: {donde}. Detalle por alumno en la "
            f"última página."))

    frios = (tot.get("desenganchados") or 0) - activos
    if frios > 0:
        nunca_ni = tot.get("nunca_entraron_ni_al_campus") or 0
        puntos.append((
            f"{frios} alumnos no aparecen ni por el campus",
            "Hace rato que no abren la materia y tampoco entran al sitio. Es otra conversación: "
            "acceso perdido, o ya no están cursando."
            + (f" {nunca_ni} no entró nunca al campus." if nunca_ni else "")))

    sin_corr = h.get("sin_corregir")
    if sin_corr:
        esperas = [(f.get("comision"), (f.get("tutor") or {}).get("nombre"),
                    f.get("espera_max_dias"), f.get("sin_corregir"))
                   for f in datos.get("por_comision", []) if f.get("sin_corregir")]
        esperas.sort(key=lambda x: -(x[2] or 0))
        c, tutor, esp, n = esperas[0]
        puntos.append((
            f"{sin_corr} entregas sin corregir en el curso",
            f"La más antigua espera hace {_dias_txt(h.get('espera_max_dias_del_curso'))} día(s), "
            f"en {c} ({tutor or 'sin tutor identificado'}, {n} en cola). Volumen no es atraso: "
            "una cola grande pero fresca está al día. A quién llamar sale de la tabla por "
            "comisión."))
    elif h.get("entregadas"):
        puntos.append((
            "No hay entregas esperando corrección",
            f"Las {h.get('entregadas')} entregas del curso están corregidas. Eso es sobre las "
            "actividades relevadas: mirá los huecos si hay alguno declarado."))

    if h.get("calificado_sin_nota"):
        puntos.append((
            f"{h['calificado_sin_nota']} entregas figuran corregidas pero SIN nota",
            "Ni pendientes ni calificadas: no salen en ninguna cola, así que nadie las está "
            "esperando. Hay que cargarles la nota a mano."))

    return puntos[:tope]


def informe_profesor_pdf(datos: dict, dest_dir: str, materia: str = "",
                         fecha: str = "", emails: bool = True) -> dict:
    """Renderiza el PDF del informe del profesor. PURA: no toca red ni cliente.

    `datos` es lo que devuelve `panorama.informe_profesor`. Recibe la fecha desde afuera a
    propósito, para que el render sea reproducible y testeable.

    Formato tomado del "Reporte Ejecutivo de Seguimiento de Tutores" que la coordinación venía
    armando a mano con scripts sueltos, **con cuatro diferencias deliberadas**:

    1. **El desenganche se mide con el reloj de la MATERIA.** Aquéllos cortaban por "21+ días
       sin entrar al CAMPUS": sobre datos medidos eso encuentra 3 de cada 30 desenganchados,
       porque al que entra todos los días para otra materia lo muestra impecable. En Prog IV el
       criterio viejo daba "0 inactivos" —y el prompt mandaba festejarlo en positivo— sobre un
       curso con 60 alumnos que no abren la materia.
    2. **No hay "Estado general: sano".** Ningún adjetivo: hechos, y los huecos arriba de los
       números. Un veredicto tranquilizador no lo audita nadie.
    3. **La tabla de riesgo va partida en dos**, porque son dos conversaciones distintas: el que
       entra al campus y no abre la materia (eligió no entrar: llamarlo primero, es el más
       recuperable) y el que no aparece por ningún lado (capaz ya no cursa).
    4. **Sale la Regional y el corte por regional**, para ver si el problema es de una sede.

    `emails=True` (por defecto) incluye el mail de cada alumno de la lista de riesgo, que es lo
    que hace el documento accionable: el destinatario tiene que poder escribirle sin volver a
    buscar a nadie. Son mails **personales** (gmail/hotmail/yahoo), no institucionales, así que
    el PDF queda en `salidas/` —fuera del árbol de git— y el pie del documento avisa que no se
    comparta fuera del equipo docente. Con `emails=False` sale la versión sin datos de contacto,
    para cuando el informe circula más lejos.
    """
    ss = getSampleStyleSheet()
    h0 = ParagraphStyle("h0", parent=ss["Heading1"], fontSize=17, leading=20, textColor=NAVY,
                        spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, textColor=NAVY,
                        spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=9.5, textColor=TEAL,
                        spaceBefore=8, spaceAfter=3)
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=8.5, leading=11.5)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7, leading=9,
                           textColor=colors.HexColor("#666666"))
    kicker = ParagraphStyle("k", parent=ss["Normal"], fontSize=7.5, leading=9,
                            textColor=TEAL, fontName="Helvetica-Bold")
    alerta = ParagraphStyle("a", parent=ss["Normal"], fontSize=8, leading=11,
                            textColor=RED)

    h = datos.get("hechos", {})
    des = datos.get("desenganche", {})
    tot = des.get("totales", {})
    meta = datos.get("_meta", {})
    titulo = materia or f"Curso {datos.get('course_id')}"
    nombre_arch = (f"informe_curso{datos.get('course_id')}"
                   + (f"_{fecha}" if fecha else "") + ".pdf")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, nombre_arch)

    E = [Paragraph("UTN · TECNICATURA UNIVERSITARIA EN PROGRAMACIÓN", kicker),
         Paragraph(f"Informe de curso — {titulo}", h0),
         Paragraph(f"Vista de coordinación · {datos.get('comisiones')} comisiones"
                   + (f" · {fecha}" if fecha else ""), small),
         Spacer(1, 12)]

    pct = h.get("pct_corregidas")
    E.append(_tiles([
        (h.get("comisiones_con_tutor", "—"), "comisiones con tutor", NAVY),
        (f"{pct}%" if pct is not None else "—", "entregas corregidas", TEAL),
        (h.get("alumnos_en_comisiones", "—"),
         "en comisiones / total del curso" if datos.get("padron", {}).get("total_del_curso")
         else "alumnos en comisiones", NAVY),
        (h.get("sin_corregir") if h.get("sin_corregir") is not None else "—",
         "entregas sin corregir", AMBER),
        (tot.get("desenganchados", "—"), "no abren la materia", RED),
    ]))
    E.append(Spacer(1, 12))

    # Los huecos ANTES de los números: si el relevamiento está incompleto, eso se lee primero.
    if meta.get("degradado"):
        E.append(Paragraph("⚠️ Este relevamiento está INCOMPLETO. Los números de arriba no "
                           "cubren todo el curso:", alerta))
        for s in meta.get("sin_dato", [])[:6]:
            E.append(Paragraph(f"· {s}", small))
        E.append(Spacer(1, 8))

    E.append(Paragraph("Qué dicen estos números", h2))
    espera = h.get("espera_max_dias_del_curso")
    E.append(Paragraph(
        f"<b>Trabajo de corrección:</b> {h.get('corregidas')} de {h.get('entregadas')} entregas "
        f"corregidas. Quedan <b>{h.get('sin_corregir')}</b> sin corregir y la más antigua espera "
        f"hace <b>{_dias_txt(espera)}</b> día(s). "
        f"{h.get('consultas_de_foro_sin_responder')} consulta(s) de foro sin responder."
        + (f" {h['calificado_sin_nota']} entrega(s) figuran corregidas pero SIN nota cargada."
           if h.get("calificado_sin_nota") else ""), body))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        f"<b>Alumnos que se están yendo:</b> <b>{tot.get('desenganchados')}</b> de "
        f"{des.get('relevados')} no abren esta materia hace {des.get('dias_desenganche')}+ días "
        f"o nunca la abrieron. De ésos, <b>{tot.get('entran_al_campus_sin_abrir_la_materia')} "
        f"entran al campus y no la abren</b> — no perdieron el acceso, eligieron no entrar, y son "
        f"los más recuperables. {tot.get('nunca_abrieron')} nunca la abrieron"
        + (f" ({tot['nunca_entraron_ni_al_campus']} no entró nunca al campus tampoco)."
           if tot.get("nunca_entraron_ni_al_campus") else ".")
        + (f" {tot['sin_dato']} no se pudieron medir." if tot.get("sin_dato") else ""), body))
    puntos = puntos_de_atencion(datos)
    if puntos:
        E.append(Spacer(1, 6))
        E.append(Paragraph("Focos de hoy", h2))
        est_t = ParagraphStyle("pt", parent=body, fontName="Helvetica-Bold", fontSize=8.5)
        colores = [RED, AMBER, NAVY, TEAL]
        rows = []
        for i, (titulo_p, detalle) in enumerate(puntos):
            num = Paragraph(f'<font color="white"><b>{i + 1}</b></font>',
                            ParagraphStyle("n", parent=body, alignment=1, fontSize=11))
            cuerpo = Table([[Paragraph(titulo_p, est_t)], [Paragraph(detalle, small)]],
                           style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 6),
                                             ("TOPPADDING", (0, 0), (-1, -1), 1),
                                             ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            rows.append([num, cuerpo])
        t = Table(rows, colWidths=[0.9 * cm, 16.1 * cm])
        est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
               ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, LT])]
        for i in range(len(rows)):
            est.append(("BACKGROUND", (0, i), (0, i), colores[i % len(colores)]))
        t.setStyle(TableStyle(est))
        E.append(t)

    E.append(Spacer(1, 8))
    E.append(Paragraph(
        "<b>Cómo leer esto.</b> No hay un veredicto en este informe a propósito: son hechos, y la "
        "conclusión la saca quien coordina. Tres advertencias. (1) Los días de desenganche son "
        "<b>sin abrir esta materia</b>, no sin entrar al campus: son dos relojes distintos y "
        "medir por el del campus pierde a la mayoría, porque el alumno que entra todos los días "
        "para otra materia aparece impecable. (2) Las filas son hechos <b>por comisión</b>; "
        "nombrar al tutor es para saber a quién llamar, no un puntaje de personas. (3) "
        "«Nunca abrió la materia» <b>no</b> es abandono confirmado: puede haberse matriculado "
        "esta semana, y este informe no ve la fecha de matriculación.", small))

    # ---- Página 2: el trabajo, comisión por comisión ----
    E.append(PageBreak())
    E.append(Paragraph("Desglose por comisión", h2))
    E.append(Paragraph(
        f"{datos.get('tareas_miradas')} de {datos.get('tareas_pedidas')} actividades relevadas. "
        "«Sin corregir» = entregada y todavía sin nota. Las dos últimas columnas son de ALUMNOS, "
        "no de corrección.", small))
    E.append(Spacer(1, 5))
    rows = [["Com.", "Tutor", "Alum.", "Entre-\ngadas", "Corre-\ngidas", "Sin\ncorregir",
             "Espera\nmáx (d)", "Demora\nmed (d)", "No abren\nla materia", "Nunca\nabrieron"]]
    for f in datos.get("por_comision", []):
        tutor = (f.get("tutor") or {}).get("nombre") or "— sin identificar —"
        rows.append([f.get("comision"), tutor[:26], f.get("alumnos"),
                     _dias_txt(f.get("entregados")), _dias_txt(f.get("corregidos")),
                     _dias_txt(f.get("sin_corregir")), _dias_txt(f.get("espera_max_dias")),
                     _dias_txt(f.get("demora_mediana_dias")),
                     _dias_txt(f.get("desenganchados")), _dias_txt(f.get("nunca_abrieron"))])
    E.append(_tabla(rows, [1.3, 4.4, 1.1, 1.35, 1.35, 1.25, 1.4, 1.4, 1.5, 1.4],
                    alinear_der=[2, 3, 4, 5, 6, 7, 8, 9]))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        "«Espera máx» es lo accionable: días que aguarda HOY la entrega sin corregir más "
        "antigua. «Demora med» es historia: lo que tardó en corregirse lo ya corregido. Volumen "
        "no es atraso — una cola grande pero de ayer está al día.", small))

    if des.get("por_regional"):
        E.append(Paragraph("Dónde se concentra el desenganche, por regional", h3))
        E.append(Paragraph(
            "Va con el total al lado a propósito: 3 de 4 y 3 de 60 no son lo mismo. Si una sede "
            "concentra, el problema puede no ser del alumno ni del tutor.", small))
        E.append(Spacer(1, 4))
        rr = [["Regional", "Alumnos", "No abren la materia", "%"]]
        for reg, c in list(des["por_regional"].items())[:10]:
            if not c["desenganchados"]:
                continue
            rr.append([reg, c["alumnos"], c["desenganchados"],
                       f"{round(100 * c['desenganchados'] / c['alumnos'])}%"])
        if len(rr) > 1:
            E.append(_tabla(rr, [7.0, 3.0, 4.0, 3.0], alinear_der=[1, 2, 3]))

    # ---- Página 3: los alumnos ----
    E.append(PageBreak())
    E.append(Paragraph("Alumnos que dejaron de abrir la materia", h2))
    E.append(Paragraph(
        f"Criterio: {des.get('criterio', {}).get('desenganchado', '')} "
        f"{des.get('criterio', {}).get('recorte', '')}", small))

    filas_al = des.get("alumnos", [])
    eligen = [a for a in filas_al if a.get("entra_al_campus_sin_abrir_la_materia")]
    resto = [a for a in filas_al if not a.get("entra_al_campus_sin_abrir_la_materia")
             and a.get("estado_aula") != "sin_dato"]
    sin_dato = [a for a in filas_al if a.get("estado_aula") == "sin_dato"]

    # Celda con salto de línea: nombres y emails largos (se vieron de 37 caracteres) se
    # desbordan de la columna si van como string pelado.
    est_celda = ParagraphStyle("cel", parent=ss["Normal"], fontSize=7, leading=8.2)

    def _bloque(titulo_b, subt, lista, color_t):
        if not lista:
            return
        E.append(Paragraph(titulo_b, ParagraphStyle("bt", parent=h3, textColor=color_t)))
        E.append(Paragraph(subt, small))
        E.append(Spacer(1, 4))
        cab = ["Alumno", "Com.", "Caso", "Sin abrir\nla materia", "Sin entrar\nal campus"]
        anchos = [6.4, 1.4, 4.2, 2.5, 2.5]
        if emails:
            cab.insert(1, "Email")
            anchos = [4.3, 4.7, 1.2, 3.15, 1.85, 1.8]
        rr = [cab]
        for a in lista:
            aula = ("Nunca" if a.get("estado_aula") == "nunca_abrio"
                    else ("—" if a.get("estado_aula") == "sin_dato"
                          else f"{a.get('dias_sin_abrir_la_materia')} d"))
            camp = ("Nunca" if a.get("dias_sin_entrar_al_campus") is None
                    else f"{a['dias_sin_entrar_al_campus']} d")
            # El caso, en la fila: al agrupar por regional los tres grupos quedan mezclados, y
            # sin esta columna no se distingue al que eligió no entrar del que ya no aparece.
            if a.get("estado_aula") == "sin_dato":
                caso = "sin dato — no medido"
            elif a.get("entra_al_campus_sin_abrir_la_materia"):
                caso = "está en el campus"
            else:
                caso = "no aparece"
            # "(sin comisión)" no entra en la columna: desbordaba encima de «Caso». Se abrevia
            # y la nota al pie lo aclara — el detalle de esos alumnos va en su propia sección.
            com = "s/com" if a.get("comision") == "(sin comisión)" else a.get("comision")
            fila = [Paragraph(a.get("nombre") or "", est_celda), Paragraph(com or "—", est_celda),
                    Paragraph(caso, est_celda), aula, camp]
            if emails:
                fila.insert(1, Paragraph(a.get("email") or "—", est_celda))
            rr.append(fila)
        E.append(_tabla(rr, anchos, alinear_der=[len(cab) - 2, len(cab) - 1]))
        E.append(Spacer(1, 6))

    E.append(Paragraph(
        "<b>Agrupado por regional</b>, y las que más concentran primero, para que el tutor nexo "
        "de cada sede abra su bloque y no tenga que filtrar la lista entera. Dentro de cada "
        "regional van primero los que <b>entran al campus y no abren la materia</b>: ésos no "
        "perdieron el acceso, eligieron no entrar — es el grupo más recuperable y el que un "
        "corte por «días sin entrar al campus» no encuentra. La columna <b>Caso</b> lo dice "
        "fila por fila.", small))
    E.append(Spacer(1, 3))
    E.append(Paragraph(
        f"En total: <b>{len(eligen)} están en el campus y no abren la materia</b> · "
        f"{len(resto)} no aparecen por el campus"
        + (f" · {len(sin_dato)} sin dato (no medidos, NO es que no la hayan abierto)"
           if sin_dato else "")
        + (f". <b>s/com</b> en la columna Com. = alumno sin comisión asignada "
           f"({pad_sc} en este curso), detalle al final."
           if (pad_sc := (datos.get("padron") or {}).get("sin_comision")) else ""), small))
    E.append(Spacer(1, 6))

    for b in des.get("por_regional_bloques", []):
        total_reg = b.get("alumnos")
        pct = (f" · {round(100 * b['desenganchados'] / total_reg)}%"
               if total_reg else "")
        _bloque(f"{b['regional']} — {b['desenganchados']}"
                + (f" de {total_reg} alumnos{pct}" if total_reg else ""),
                "", b["lista"], RED if b["desenganchados"] >= 10 else AMBER)

    if not filas_al:
        E.append(Paragraph(
            f"Ninguno de los {des.get('relevados')} alumnos relevados dejó de abrir la materia "
            f"por {des.get('dias_desenganche')}+ días. Ojo: eso es sobre los relevados — mirá los "
            "huecos de la primera página antes de leerlo como cobertura total.", body))

    pad = datos.get("padron") or {}
    if pad.get("alumnos_sin_comision"):
        E.append(Paragraph(
            f"Alumnos SIN comisión ({pad['sin_comision']}) — no los ve ningún tutor",
            ParagraphStyle("sc", parent=h3, textColor=RED)))
        E.append(Paragraph(
            "Están matriculados en el curso y en su regional, pero en ninguna comisión. Toda "
            "vista por comisión —la de cada tutor incluida— los saltea por construcción. Van "
            "medidos arriba con la etiqueta «(sin comisión)».", small))
        E.append(Spacer(1, 4))
        cab = ["Alumno", "Regional"]
        anchos = [10.0, 7.0]
        if emails:
            cab.insert(1, "Email")
            anchos = [6.0, 6.0, 5.0]
        rr = [cab]
        for a in pad["alumnos_sin_comision"]:
            f = [Paragraph(a.get("nombre") or "", est_celda), a.get("regional") or "—"]
            if emails:
                f.insert(1, Paragraph(a.get("email") or "—", est_celda))
            rr.append(f)
        E.append(_tabla(rr, anchos))
        E.append(Spacer(1, 6))

    E.append(Paragraph("Padrón, comisiones y foros", h2))
    ign = datos.get("grupos_ignorados") or []
    cuadre = (
        f"<b>Padrón:</b> {pad.get('en_comisiones')} alumnos en las {datos.get('comisiones')} "
        f"comisiones sobre {pad['total_del_curso']} matriculados en el curso"
        + (f", y {pad['sin_comision']} sin comisión." if pad.get("sin_comision")
           else " — cuadra.")
        + ("" if pad.get("cuadra") else " ⚠️ La cuenta NO cierra: puede haber alguien "
           "matriculado en dos comisiones, contado dos veces.")
    ) if pad.get("total_del_curso") else (
        f"<b>Padrón:</b> las {datos.get('comisiones')} comisiones suman "
        f"{pad.get('en_comisiones')} alumnos. NO se pudo contrastar contra el total del curso, "
        "así que puede haber alumnos sin comisión que este informe no ve.")
    for b in [cuadre,
              f"<b>Tutores:</b> {h.get('comisiones_con_tutor')} comisiones con tutor "
              "identificado (docente = todo el que no es alumno; el rol no es uniforme en este "
              "campus).",
              f"<b>Foros:</b> {h.get('consultas_de_foro_sin_responder')} consulta(s) de alumnos "
              "sin ninguna respuesta. Sólo se cuenta lo verificable —hilos con cero réplicas—: "
              "el campus no dice quién debía contestar.",
              f"<b>Grupos que no son comisión:</b> {len(ign)} (regionales y auxiliares). "
              "Las regionales se usan para la sede de cada alumno."]:
        E.append(Paragraph(f"· {b}", body))
        E.append(Spacer(1, 2))

    E.append(Spacer(1, 8))
    if emails:
        # El aviso va DENTRO del PDF, no sólo en la respuesta de la tool: el archivo se reenvía
        # solo, sin el contexto en el que se generó.
        E.append(Paragraph(
            "⚠️ Este documento incluye datos de contacto de alumnos (mails personales). No lo "
            "subas a repositorios ni lo compartas fuera del equipo docente.", alerta))
        E.append(Spacer(1, 4))
    E.append(Paragraph(
        f"UTN · {titulo} · datos leídos EN VIVO de la API REST del campus"
        + (f" el {fecha}" if fecha else "")
        + f" · relevamiento en {meta.get('segundos')} s · vista de coordinación: hechos por "
        "comisión, sin ranking de personas.", small))

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Informe de curso — {titulo}", author="tup-campus-navigator")
    doc.build(E)
    return {
        "archivo": path,
        # El número REAL de páginas, de reportlab. Estaba puesto "3" fijo porque el formato de
        # referencia tenía tres, y con 50 alumnos en la tabla de riesgo el documento salía en 4:
        # un dato decorativo que ya era falso en la primera corrida.
        "paginas": getattr(doc, "page", None),
        "incluye_emails": bool(emails),
        "degradado": bool(meta.get("degradado")),
    }
