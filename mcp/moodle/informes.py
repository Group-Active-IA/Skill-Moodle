"""Informes PDF de la skill, 100% sobre la API REST.

Dos documentos distintos y con destinatarios distintos:

- `informe_pendientes` — para el TUTOR: qué le falta corregir en su comisión. El copiloto lo
  armaba con `actions.py` (scraping Playwright); acá se reescribe contra `ws_api`
  (mod_assign_*): mismas columnas y layout, pero sin navegador.
- `informe_nexos_pdf` — para los TUTORES NEXO: los alumnos que dejaron de abrir la materia,
  agrupados por regional, con el nexo de cada sede. Habla de ALUMNOS y de nadie más.

Los dos son funciones PURAS de renderizado en la parte que importa (reciben el dict ya armado,
no el cliente), así que se pueden testear sin red y sin credenciales — que es donde este
proyecto encuentra los bugs.

**Por qué el informe de nexos NO trae el trabajo de corrección de los tutores.** Estaban juntos
en un mismo PDF y el costo no era de formato: un tutor que abre un documento donde su comisión
aparece medida al lado de una lista de alumnos lo lee como una evaluación suya. Son dos informes
con dos destinatarios distintos — los alumnos van al nexo de su sede, el trabajo docente va a
coordinación (`reporte_coordinacion`) — y separarlos es lo que deja hablar de cada cosa sin ruido.
"""

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
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
            f"<b>Atención:</b> {len(no_relevadas)} tarea(s) NO se pudieron consultar y quedaron FUERA de "
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
# PDF de NEXOS — render PURO del dict de `panorama.informe_nexos`
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
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
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
           alinear_der: list[int] | None = None, compacta: bool = False) -> Table:
    """Tabla del informe. `compacta` achica el padding vertical.

    Existe por una razón de contenido, no de estética: un curso con 16 comisiones tiene el doble
    de filas que uno con 8, y con el padding normal la página 2 se partía en dos y el informe
    pasaba de 3 páginas a 4. El largo del informe no puede depender de cuántas comisiones tiene
    la materia — es el mismo documento y se lee igual.
    """
    t = Table(rows, colWidths=[a * cm for a in anchos], repeatRows=1)
    est = [("BACKGROUND", (0, 0), (-1, 0), NAVY),
           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
           ("FONTSIZE", (0, 0), (-1, -1), font),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("GRID", (0, 0), (-1, -1), 0.3, GREY),
           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LT]),
           ("TOPPADDING", (0, 0), (-1, -1), 1.5 if compacta else 3),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5 if compacta else 3)]
    for c in (alinear_der or []):
        est.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(est))
    return t


def focos_de_alumnos(datos: dict, tope: int = 4) -> list[tuple]:
    """PURA: los focos del informe de nexos, calculados de los datos. -> [(titulo, detalle)].

    En el informe que se venía armando a mano este bloque lo escribía un modelo a partir de los
    números, y salía "Estado general: sano" sobre un curso con 60 alumnos que no abrían la
    materia. Acá se calcula, y estos son hechos con su cuenta al lado: **ningún adjetivo, ningún
    estado general.** Un número raro se revisa; un "sano" no lo revisa nadie.

    El orden es por accionabilidad, no por gravedad: primero el grupo recuperable.
    """
    des = datos.get("desenganche") or {}
    tot = des.get("totales") or {}
    pad = datos.get("padron") or {}
    puntos: list[tuple] = []

    activos = tot.get("entran_al_campus_sin_abrir_la_materia") or 0
    if activos:
        top = [(b["regional"], sum(1 for a in b["lista"]
                                   if a.get("entra_al_campus_sin_abrir_la_materia")))
               for b in des.get("por_regional_bloques", [])]
        top = sorted((t for t in top if t[1]), key=lambda kv: -kv[1])[:3]
        puntos.append((
            f"{activos} alumnos entran al campus y NO abren la materia",
            "Están usando el campus para otra cosa: no perdieron el acceso, eligieron no entrar "
            "acá. Es el grupo más recuperable y el que un corte por «días sin entrar al campus» "
            "no encuentra. Donde más se concentra: "
            + ", ".join(f"{r} ({n})" for r, n in top) + "."))

    frios = (tot.get("desenganchados") or 0) - activos
    if frios > 0:
        nunca_ni = tot.get("nunca_entraron_ni_al_campus") or 0
        puntos.append((
            f"{frios} alumnos no aparecen ni por el campus",
            "Hace rato que no abren la materia y tampoco entran al sitio. Es otra conversación: "
            "acceso perdido, o ya no están cursando."
            + (f" {nunca_ni} no entró nunca al campus." if nunca_ni else "")))

    if pad.get("sin_comision"):
        puntos.append((
            f"{pad['sin_comision']} alumnos sin comisión asignada",
            "Matriculados en el curso y en su regional, pero en ninguna comisión: no los ve "
            "ningún tutor, porque todas las vistas del campus trabajan por comisión. Si recién "
            "se matricularon es normal — entran así hasta que alguien los asigna."))

    if tot.get("sin_dato"):
        puntos.append((
            f"{tot['sin_dato']} alumnos no se pudieron medir",
            "El campus no devolvió su último acceso a la materia. NO significa que no la hayan "
            "abierto: significa que no se sabe. El relevamiento no cubre a todo el padrón."))

    return puntos[:tope]


def informe_nexos_pdf(datos: dict, dest_dir: str, materia: str = "",
                      fecha: str = "", emails: bool = True) -> dict:
    """Renderiza el informe de NEXOS. PURA: no toca red ni cliente.

    Un PDF por materia, con los alumnos que dejaron de abrir esa materia **agrupados por
    regional** y el Tutor Nexo de cada sede arriba de su bloque. Es lo que el nexo necesita
    para hacer seguimiento: abre su bloque y ahí está su gente, con mail y con el caso de cada
    uno.

    **No trae nada del trabajo de corrección de los tutores.** Eso vive en
    `reporte_coordinacion` y va a coordinación. Estaban juntos en un mismo documento y el
    problema no era el largo: un tutor que abre un informe donde su comisión aparece medida al
    lado de una lista de alumnos lo lee como una evaluación suya.

    Tampoco emite veredicto. El informe que se venía armando a mano abría con "Estado general:
    sano" sobre un curso con 60 alumnos que no abrían la materia — el adjetivo salía de cortar
    la inactividad por el reloj del campus, que pierde ~90% de los casos. Acá van los hechos y
    los huecos; la conclusión la saca quien lee.
    """
    ss = getSampleStyleSheet()
    h0 = ParagraphStyle("h0", parent=ss["Heading1"], fontSize=17, leading=20, textColor=NAVY,
                        spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, textColor=NAVY,
                        spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=10, textColor=RED,
                        spaceBefore=9, spaceAfter=1)
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=8.5, leading=11.5)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7, leading=9,
                           textColor=colors.HexColor("#666666"))
    kicker = ParagraphStyle("k", parent=ss["Normal"], fontSize=7.5, leading=9,
                            textColor=TEAL, fontName="Helvetica-Bold")
    alerta = ParagraphStyle("a", parent=ss["Normal"], fontSize=8, leading=11, textColor=RED)
    est_celda = ParagraphStyle("cel", parent=ss["Normal"], fontSize=7, leading=8.2)
    est_nexo = ParagraphStyle("nx", parent=ss["Normal"], fontSize=8, leading=10,
                              textColor=NAVY)

    h = datos.get("hechos", {})
    des = datos.get("desenganche", {})
    tot = des.get("totales", {})
    pad = datos.get("padron") or {}
    meta = datos.get("_meta", {})
    titulo = materia or f"Curso {datos.get('course_id')}"
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"informe_nexos_curso{datos.get('course_id')}"
                        + (f"_{fecha}" if fecha else "") + ".pdf")

    E = [Paragraph("UTN · TECNICATURA UNIVERSITARIA EN PROGRAMACIÓN", kicker),
         Paragraph(f"Seguimiento de alumnos — {titulo}", h0),
         Paragraph("Informe para Tutores Nexo · alumnos que dejaron de abrir la materia"
                   + (f" · {fecha}" if fecha else ""), small),
         Spacer(1, 12)]

    E.append(_tiles([
        (h.get("alumnos_relevados", "—"), "alumnos relevados / total del curso", NAVY),
        (tot.get("desenganchados", "—"), "no abren la materia", RED),
        (tot.get("entran_al_campus_sin_abrir_la_materia", "—"),
         "entran al campus y no la abren", AMBER),
        (tot.get("nunca_abrieron", "—"), "nunca la abrieron", NAVY),
        ((des.get("retraso") or {}).get("retrasados", "—"), "retrasados", AMBER)
        if des.get("retraso") else (datos.get("regionales", "—"), "regionales", TEAL),
    ]))
    E.append(Spacer(1, 12))

    if meta.get("degradado"):
        # El encabezado NO afirma QUÉ está incompleto: los huecos son de distinta naturaleza
        # —uno puede ser "faltan alumnos por medir" y otro "no se calculó una columna"— y decir
        # "los números no cubren todo el curso" sobre el segundo es una afirmación falsa, justo
        # en el renglón donde el informe promete ser exacto. Cada hueco se explica solo abajo.
        E.append(Paragraph("<b>Atención:</b> leé estos huecos antes que los números:", alerta))
        for s in meta.get("sin_dato", [])[:6]:
            E.append(Paragraph(f"· {sin_emoji(s)}", small))
        E.append(Spacer(1, 8))

    E.append(Paragraph("Qué mide este informe", h2))
    E.append(Paragraph(
        f"De {des.get('relevados')} alumnos relevados, <b>{tot.get('desenganchados')}</b> no "
        f"abren esta materia hace {des.get('dias_desenganche')}+ días o nunca la abrieron. "
        f"De ésos, <b>{tot.get('entran_al_campus_sin_abrir_la_materia')} entran al campus y no "
        "la abren</b>: no perdieron el acceso, eligieron no entrar acá — es el grupo más "
        f"recuperable. {tot.get('nunca_abrieron')} nunca la abrieron"
        + (f", y {tot['nunca_entraron_ni_al_campus']} de ésos no entró nunca al campus tampoco."
           if tot.get("nunca_entraron_ni_al_campus") else ".")
        + (f" {tot['sin_dato']} no se pudieron medir." if tot.get("sin_dato") else ""), body))
    E.append(Spacer(1, 5))
    E.append(Paragraph(
        "<b>Los días son SIN ABRIR ESTA MATERIA, no sin entrar al campus.</b> Son dos relojes "
        "distintos: el alumno que entra a Moodle todos los días para otra materia y nunca abre "
        "ésta aparece impecable si se mira el del campus. Las dos columnas están en cada fila "
        "para poder distinguirlo. Y <b>«nunca abrió la materia» no es abandono confirmado</b>: "
        "puede haberse matriculado esta semana, y este informe no ve la fecha de matriculación.",
        small))
    E.append(Spacer(1, 4))
    ret0 = des.get("retraso") or {}
    if ret0:
        E.append(Spacer(1, 4))
        E.append(Paragraph(
            f"<b>Retraso ({ret0['etiqueta']})</b> = le falta entregar al menos una de las "
            f"actividades de cierre de las unidades {', '.join('U' + str(u) for u in ret0['unidades_medidas'])}, "
            "que son las ya exigibles a la fecha. <b>Basta que falte una para que diga «Sí»</b>; "
            "«No» significa que están todas. El rango lo fija el tutor, no el campus: Moodle no "
            "expone qué unidad se está cursando, así que si no se lo indica, esta columna no "
            "sale — nunca se estima.", small))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        "Este informe habla de <b>alumnos</b> y de nadie más: no trae ninguna medición del "
        "trabajo de los tutores.", small))

    focos = focos_de_alumnos(datos)
    if focos:
        E.append(Paragraph("Focos de hoy", h2))
        # Se usa la función compartida: este bloque estaba copiado y por eso un reemplazo
        # pensado para el otro lo dejó llamando a una variable que acá no existe. Un bloque
        # duplicado no falla el día que se copia — falla el día que se toca el original.
        E.append(_bloque_focos(focos, body, small))

    # ---- Los bloques por regional ----
    E.append(PageBreak())
    E.append(Paragraph("Alumnos por regional", h2))
    E.append(Paragraph(
        "Las regionales que más concentran, primero. Dentro de cada una van primero los que "
        "<b>entran al campus y no abren la materia</b> — ésos eligieron no entrar y son los más "
        "recuperables. La columna <b>Caso</b> lo dice fila por fila. <b>s/com</b> = alumno sin "
        "comisión asignada.", small))
    E.append(Spacer(1, 6))

    for b in des.get("por_regional_bloques", []):
        total_reg = b.get("alumnos")
        pct = f" · {round(100 * b['desenganchados'] / total_reg)}%" if total_reg else ""
        E.append(Paragraph(f"{b['regional']} — {b['desenganchados']}"
                           + (f" de {total_reg} alumnos{pct}" if total_reg else ""), h3))
        nx = b.get("nexo")
        if nx:
            E.append(Paragraph(
                f"Tutor Nexo: <b>{', '.join(nx.get('nexos') or []) or '—'}</b>"
                + (f" · {' · '.join(nx.get('mails') or [])}" if nx.get("mails") else "")
                + (f" · {nx.get('facultad')}" if nx.get("facultad") else ""), est_nexo))
        else:
            E.append(Paragraph("Tutor Nexo: no está en el catálogo de la skill "
                               "(mcp/nexos.json). Sin contacto para esta regional.", alerta))
        E.append(Spacer(1, 4))

        ret = des.get("retraso") or {}
        cab = ["Alumno", "Com.", "Situación", "Sin abrir\nla materia",
               "Sin entrar\nal campus"]
        anchos = [6.0, 1.3, 4.7, 2.5, 2.5]
        if emails:
            cab.insert(1, "Email")
            anchos = [4.0, 4.4, 1.15, 4.0, 1.75, 1.7]
        if ret:
            cab.insert(-2, f"Retraso\n({ret['etiqueta']})")
            anchos = ([3.4, 3.9, 1.35, 3.35, 1.35, 1.8, 1.85] if emails
                      else [5.3, 1.4, 4.1, 1.5, 2.35, 2.35])
        rr = [cab]
        for a in b["lista"]:
            aula = ("Nunca" if a.get("estado_aula") == "nunca_abrio"
                    else ("—" if a.get("estado_aula") == "sin_dato"
                          else f"{a.get('dias_sin_abrir_la_materia')} d"))
            camp = ("Nunca" if a.get("dias_sin_entrar_al_campus") is None
                    else f"{a['dias_sin_entrar_al_campus']} d")
            # Los valores tienen que explicarse solos: preguntaron qué significaba "Caso" y
            # la explicación estaba en la intro de la sección, tres páginas antes. Un informe
            # que hay que explicar aparte no se lee.
            if a.get("estado_aula") == "sin_dato":
                caso = "sin dato: no se pudo medir"
            elif a.get("entra_al_campus_sin_abrir_la_materia"):
                caso = "entra al campus, no abre la materia"
            else:
                caso = "no entra al campus"
            com = "s/com" if a.get("comision") == "(sin comisión)" else a.get("comision")
            fila = [Paragraph(a.get("nombre") or "", est_celda),
                    Paragraph(com or "—", est_celda), Paragraph(caso, est_celda), aula, camp]
            if ret:
                # Sí en rojo, No en verde. El color va con la palabra, nunca solo: el informe
                # se imprime en blanco y negro y se fotocopia.
                r_ = a.get("retraso")
                txt = ("—" if r_ is None
                       else (f'<font color="#a02c2c"><b>Sí</b></font>' if r_
                             else '<font color="#1f7a6c">No</font>'))
                falt = a.get("unidades_faltantes") or []
                if r_ and falt:
                    txt += f' <font size="5.6">U{",U".join(str(u) for u in falt)}</font>'
                fila.insert(-2, Paragraph(txt, est_celda))
            if emails:
                fila.insert(1, Paragraph(a.get("email") or "—", est_celda))
            rr.append(fila)
        E.append(_tabla(rr, anchos, alinear_der=[len(cab) - 2, len(cab) - 1]))
        E.append(Spacer(1, 6))

    if not des.get("por_regional_bloques"):
        E.append(Paragraph(
            f"Ninguno de los {des.get('relevados')} alumnos relevados dejó de abrir la materia "
            f"por {des.get('dias_desenganche')}+ días. Ojo: es sobre los relevados — mirá los "
            "huecos de la primera página antes de leerlo como cobertura total.", body))

    # ---- Alumnos sin comisión ----
    if pad.get("alumnos_sin_comision"):
        E.append(Paragraph(f"Alumnos SIN comisión ({pad['sin_comision']}) — no los ve "
                           "ningún tutor", h3))
        E.append(Paragraph(
            "Están matriculados en el curso y en su regional, pero en ninguna comisión. Toda "
            "vista por comisión —la de cada tutor incluida— los saltea por construcción. Si "
            "recién se matricularon es normal: entran sin comisión hasta que alguien los "
            "asigna.", small))
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

    E.append(Paragraph("Padrón", h2))
    cuadre = (
        f"<b>{pad.get('en_comisiones')}</b> alumnos en las {datos.get('comisiones')} comisiones "
        f"sobre <b>{pad['total_del_curso']}</b> matriculados en el curso"
        + (f", y {pad['sin_comision']} sin comisión." if pad.get("sin_comision")
           else " — cuadra.")
        + ("" if pad.get("cuadra") else " <b>Atención:</b> la cuenta NO cierra: puede haber alguien "
           "matriculado en dos comisiones, contado dos veces.")
    ) if pad.get("total_del_curso") else (
        f"Las {datos.get('comisiones')} comisiones suman {pad.get('en_comisiones')} alumnos. "
        "NO se pudo contrastar contra el total del curso, así que puede haber alumnos sin "
        "comisión que este informe no ve.")
    E.append(Paragraph(f"· {cuadre}", body))
    E.append(Paragraph(
        f"· Tutores Nexo en el catálogo de la skill: {meta.get('nexos_en_catalogo')} "
        f"regionales." + (f" Sin nexo: {', '.join(meta['regionales_sin_nexo'])}."
                          if meta.get("regionales_sin_nexo") else ""), body))

    E.append(Spacer(1, 8))
    if emails:
        E.append(Paragraph(
            "<b>Atención:</b> este documento incluye datos de contacto de alumnos (mails personales). No lo "
            "subas a repositorios ni lo compartas fuera del equipo docente.", alerta))
        E.append(Spacer(1, 4))
    E.append(Paragraph(
        f"UTN · {titulo} · datos leídos EN VIVO de la API REST del campus"
        + (f" el {fecha}" if fecha else "")
        + f" · relevamiento en {meta.get('segundos')} s · este informe mide ALUMNOS, no "
        "el trabajo de los tutores.", small))

    def _pie(canvas, doc_):
        """Leyenda de la columna Situación, en CADA página.

        Va al pie y no en la intro porque el informe se lee salteado: el nexo abre el PDF,
        busca su regional y cae en la página 5. La explicación tiene que estar donde está
        mirando, no tres páginas antes."""
        canvas.saveState()
        canvas.setFont("Helvetica", 6.2)
        canvas.setFillColor(colors.HexColor("#888888"))
        # El número de página se dibuja primero y la leyenda se corta ANTES de llegar a él.
        # Sin ese tope los dos textos se superponen y quedan ilegibles los dos.
        canvas.drawRightString(19.0 * cm, 0.85 * cm, f"pág. {doc_.page}")
        # Red de seguridad: si algún día la leyenda crece, se recorta con puntos suspensivos
        # antes de llegar al número de página. Cortada a mitad de palabra parece un bug.
        leyenda, tope = LEYENDA_SITUACION, LEYENDA_ANCHO_MAX
        while canvas.stringWidth(leyenda, "Helvetica", 6.2) > tope and " " in leyenda:
            leyenda = leyenda.rsplit(" ", 1)[0] + "…"
        canvas.drawString(2.0 * cm, 0.85 * cm, leyenda)
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.7 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Seguimiento de alumnos — {titulo}",
                            author="tup-campus-navigator")
    doc.build(E, onFirstPage=_pie, onLaterPages=_pie)
    return {"archivo": path, "paginas": getattr(doc, "page", None),
            "incluye_emails": bool(emails), "degradado": bool(meta.get("degradado"))}


# ---------------------------------------------------------------------------
# PDF de AVANCE — cómo van los alumnos con las entregas. Render PURO.
# ---------------------------------------------------------------------------

def _dias_txt(d) -> str:
    """Número para mostrar. `None` -> «—», y los decimales con COMA.

    La coma no es un capricho de estilo: el informe lo leen tutores y coordinación en
    castellano, y «2.7» al lado de una tabla con miles se lee mal. Los enteros (conteos) salen
    tal cual — sólo los días llevan decimal.
    """
    if d is None:
        return "—"
    return f"{d:g}".replace(".", ",") if isinstance(d, float) else str(d)


def _fecha_txt(ts) -> str:
    """Timestamp -> «14 jul 2026». 0/None -> «Nunca». PURA.

    Va la fecha además de los días porque son para cosas distintas: los días ordenan la lista,
    la fecha es lo que se le nombra al alumno cuando se lo contacta. El «~» del informe que se
    armaba a mano se saca: la fecha es exacta, no aproximada.
    """
    if not ts:
        return "Nunca"
    from datetime import datetime
    m = ("ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic")
    d = datetime.fromtimestamp(int(ts))
    return f"{d.day} {m[d.month - 1]} {d.year}"


def sin_emoji(txt: str) -> str:
    """Quita lo que Helvetica no sabe dibujar. PURA.

    Los títulos del campus vienen con emoji ("Actividad de cierre unidad 5 🎯🏁") y reportlab
    los pinta como cuadraditos negros: en la tabla parecían un dato. Se filtra por lo que entra
    en latin-1, así que acentos y ñ quedan.
    """
    out = []
    for c in (txt or ""):
        try:
            c.encode("latin-1")
        except UnicodeEncodeError:
            continue
        out.append(c)
    return " ".join("".join(out).split())


# Umbrales de la columna "Nota" de la tabla por tutor, en días de espera. Existen porque el
# conteo de pendientes solo NO se puede leer: en el mismo informe un tutor con 8 en cola está
# mejor que otro con 5, y la tabla no lo decía — había que hacer la cuenta mental fila por fila.
_ESPERA_FRESCA = 1.0    # entregado prácticamente hoy: no hay nada que priorizar
_ESPERA_PRIORIZAR = 3.0  # a partir de acá el alumno ya nota que no le contestaron


def nota_de_cola(pendientes, espera_dias) -> str:
    """PURA: traduce (pendientes, espera) a qué hacer con esa cola. -> texto corto.

    Es lo único del informe que interpreta en vez de contar, y se banca porque la regla es
    explícita y pareja para todos: **la espera manda, el volumen no**. Una cola de 8 entregas de
    ayer está al día; una sola de hace tres semanas no. Sin esta columna las dos se leen igual.

    No califica a nadie: describe el estado de una cola, que es un hecho del trabajo. Por eso
    dice "priorizar" —qué hacer— y nunca "lento" ni "atrasado" —cómo es la persona.
    """
    if not pendientes:
        return "sin cola"
    if espera_dias is None:
        return "sin dato de espera"
    if espera_dias >= _ESPERA_PRIORIZAR:
        return f"espera de {_dias_txt(espera_dias)} d, priorizar"
    if espera_dias < _ESPERA_FRESCA:
        return "cola fresca (espera < 1 d)"
    return f"al día (espera {_dias_txt(espera_dias)} d)"


def focos_de_correccion(datos: dict, tope: int = 6) -> list[tuple]:
    """PURA: los puntos de atención del día. -> [(titulo, detalle)].

    Hechos con su cuenta al lado y a quién llamar. **Sin veredicto**: no hay "estado general",
    no hay adjetivo sobre el curso ni sobre nadie. El informe que se armaba a mano abría con
    "Estado general: sano" sobre un curso con 60 alumnos que no abrían la materia — un número
    raro alguien lo revisa, un "sano" no lo revisa nadie, y si el resumen dice que está bien la
    página del detalle no la abre nadie tampoco.

    Se nombra la comisión y a su tutor porque sin eso no se sabe a quién llamar, que es para lo
    que sirve. Nombrar no es puntuar: no hay podio, no hay nota y no hay orden de mejor a peor.

    El último punto es siempre **lo que está funcionando**, y no es cortesía: un informe diario
    que sólo lista problemas se lee como una acusación y deja de leerse. Es un hecho contable —
    comisiones sin cola— no un elogio.
    """
    filas = datos.get("filas") or []
    act = datos.get("por_actividad") or []
    des = (datos.get("desenganche") or {}).get("totales") or {}
    puntos: list[tuple] = []

    def _quien(f) -> str:
        return (f.get("tutor") or {}).get("nombre") or "sin tutor identificado"

    con_cola = [f for f in filas if f.get("sin_corregir")]
    if con_cola:
        total = sum(f["sin_corregir"] for f in con_cola)
        peor = max(con_cola, key=lambda f: (f.get("espera_max_dias") or 0))
        puntos.append((
            f"{peor.get('comision')} ({_quien(peor)}) — la corrección que más espera",
            f"{peor['sin_corregir']} entrega(s) sin corregir y la más antigua aguarda hace "
            f"{_dias_txt(peor.get('espera_max_dias'))} d — la espera más larga del curso. "
            f"En total hay {total} sin corregir en {len(con_cola)} comisión(es). "
            "Volumen no es atraso: una cola grande pero de ayer está al día, así que lo "
            "accionable es la espera y no el conteo."))

        # La de más VOLUMEN, sólo si es otra. Si es la misma comisión, repetirla ocupa un punto
        # de los seis y no agrega un dato.
        #
        # El EMPATE se declara. Un `max()` a secas devuelve la primera y el título decía "la
        # cola más alta" cuando había cuatro comisiones con la misma cantidad: nombraba a una
        # sola persona por un desempate de orden alfabético. Un título que la tabla de al lado
        # desmiente es la forma más barata de que nadie le crea al informe.
        tope_vol = max(f["sin_corregir"] for f in con_cola)
        empatadas = [f for f in con_cola if f["sin_corregir"] == tope_vol]
        mas = max(empatadas, key=lambda f: (f.get("espera_max_dias") or 0))
        if mas is not peor:
            if len(empatadas) > 1:
                titulo = (f"{len(empatadas)} comisiones comparten la cola más alta "
                          f"({tope_vol} cada una)")
                detalle = ("Son " + ", ".join(f"{f.get('comision')} ({_quien(f)})"
                                              for f in empatadas)
                           + f". La que más espera de ésas es {mas.get('comision')}, con "
                           f"{_dias_txt(mas.get('espera_max_dias'))} d. ")
            else:
                titulo = f"{mas.get('comision')} ({_quien(mas)}) — la cola más alta"
                detalle = (f"{mas['sin_corregir']} entregas sin corregir, con una espera máxima "
                           f"de {_dias_txt(mas.get('espera_max_dias'))} d. ")
            puntos.append((
                titulo,
                detalle + nota_de_cola(mas["sin_corregir"],
                                       mas.get("espera_max_dias")).capitalize() + "."))
    elif datos.get("tareas_miradas"):
        puntos.append((
            "No hay entregas esperando corrección",
            f"Está corregido todo lo entregado en las {datos['tareas_miradas']} actividades "
            "miradas. Es sobre esas actividades: mirá los huecos si hay alguno declarado."))

    # CADENCIA: cuánto llegó a tardar lo que YA se corrigió. Es historia y no una cola, así que
    # el punto lo dice explícito — si no, un máximo alto se lee como trabajo pendiente. Sirve
    # para lo que la espera no muestra: una comisión puede estar sin cola hoy y aun así haber
    # tenido una entrega que esperó una semana.
    con_demora = [f for f in filas if f.get("demora_max_dias") is not None]
    if con_demora:
        lenta = max(con_demora, key=lambda f: f["demora_max_dias"])
        if lenta["demora_max_dias"] >= _ESPERA_PRIORIZAR:
            puntos.append((
                f"{lenta.get('comision')} ({_quien(lenta)}) — la demora más larga ya corregida",
                f"Sobre lo que ya tiene nota, alguna entrega llegó a esperar "
                f"{_dias_txt(lenta['demora_max_dias'])} d (mediana de la comisión: "
                f"{_dias_txt(lenta.get('demora_mediana_dias'))} d). "
                f"Hoy tiene {lenta.get('sin_corregir') or 0} en cola: <b>es cadencia histórica, "
                "no un pendiente</b>. Va como referencia del ritmo, no como algo a resolver hoy."))

    sin_nota = sum(f.get("calificado_sin_nota") or 0 for f in filas)
    if sin_nota:
        puntos.append((
            f"{sin_nota} entregas figuran corregidas pero SIN nota",
            "Ni pendientes ni calificadas: no salen en ninguna cola, así que nadie las está "
            "esperando — ni el tutor ni el alumno. Hay que cargarles la nota a mano."))

    # DESENGANCHE con el reloj de la materia. El corte por "días sin pisar el campus" que se
    # usaba antes encontraba 3 de cada 30, y siempre para el lado que tranquiliza.
    if des.get("desenganchados"):
        activos = des.get("entran_al_campus_sin_abrir_la_materia") or 0
        puntos.append((
            f"{des['desenganchados']} alumnos no abren la materia",
            f"Medido con el reloj de ESTA materia, no con el del campus. De ésos, {activos} "
            "entran al campus y no la abren: no perdieron el acceso, eligieron no entrar — es "
            "el grupo más recuperable, y el que un corte por «días sin entrar al campus» no "
            "encuentra. El detalle con mails y el nexo de cada sede va en el informe de nexos."))

    foros = sum(f.get("consultas_sin_responder") or 0 for f in filas)
    if foros:
        puntos.append((
            f"{foros} consulta(s) de foro sin ninguna respuesta",
            "Hilos abiertos por alumnos donde no contestó nadie — ni un docente ni otro alumno. "
            "El campus no dice quién debía responder, así que el informe cuenta el silencio y "
            "no se lo adjudica a nadie."))

    sf = datos.get("actividades_sin_fecha_de_entrega") or 0
    if sf:
        puntos.append((
            f"{sf} de {len(act)} actividades no tienen fecha de entrega",
            "Sin `duedate` no se puede distinguir «no entregó» de «todavía no vencía». Cuidado "
            "al leer cualquier conteo de faltantes como abandono: es lo que hace que la lista "
            "de riesgo marque al padrón entero."))

    # Lo que funciona va SIEMPRE último y siempre entra, aunque el tope corte lo de arriba.
    sin_cola = [f for f in filas if f.get("sin_corregir") == 0]
    if sin_cola and con_cola:
        funciona = (
            "Lo que está al día",
            f"{len(sin_cola)} de {len(filas)} comisiones no tienen ninguna entrega en cola: "
            + ", ".join(str(f.get("comision")) for f in sin_cola[:8])
            + ("…" if len(sin_cola) > 8 else "") + ".")
        return puntos[:max(tope - 1, 0)] + [funciona]

    return puntos[:tope]


def _caja(texto: str, estilo, ancho_total: float = 17.0, color=NAVY) -> Table:
    """Caja de resumen: fondo claro y una barra de color a la izquierda.

    Lo que va acá son HECHOS con su número, nunca un veredicto. La versión de este informe que
    se armaba a mano abría con "Estado general: sano" sobre un curso con 60 alumnos que no
    abrían la materia: el adjetivo salía de cortar la inactividad por el reloj del campus, que
    pierde la mayoría de los casos. Un número raro alguien lo revisa; un "sano" no lo revisa
    nadie, y si el resumen dice que está bien el resto del informe no se abre.
    """
    t = Table([[Paragraph(texto, estilo)]], colWidths=[ancho_total * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef3f7")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


# La leyenda del pie y el ancho que tiene disponible antes de chocar con el número de página.
# Van como constantes para poder MEDIRLAS en un test: `canvas.drawString` no recorta ni hace
# wrap — dibuja encima —, así que una leyenda que crece se monta sobre el número de página y
# quedan ilegibles los dos. Ya pasó.
LEYENDA_SITUACION = ("Situación: «entra al campus, no abre la materia» = eligió no entrar, es "
                     "el más recuperable · «no entra al campus» = tampoco aparece por Moodle.")
LEYENDA_ANCHO_MAX = 16.2 * cm

# Cuántos alumnos desenganchados entran en la tabla de la página 3. El listado completo, con
# mail y con el nexo de cada sede, es el informe de nexos: éste es el de coordinación y sólo
# necesita el tamaño del problema y por dónde empezar. Se declara siempre cuántos quedaron
# afuera — un recorte silencioso se lee como cobertura total.
_MAX_DESENGANCHE_EN_TABLA = 25


def reporte_coordinacion_pdf(datos: dict, dest_dir: str, materia: str = "", fecha: str = "",
                 anexo: bool = False) -> dict:
    """Renderiza el reporte de coordinación sobre el trabajo de corrección. PURA.

    Tres páginas y se lee en dos minutos: KPIs y puntos de atención, el desglose por comisión y
    por tutor, y el riesgo de desenganche. Ese largo es la especificación, no una consecuencia:
    es un informe DIARIO, y uno que no se lee no existe. El detalle largo —una fila por cada
    entrega esperando, por actividad, hilo por hilo— pasó a un anexo que sale sólo con
    `anexo=True`: en un curso movido esas listas solas se comían tres páginas.

    **Nombra a todos los tutores y no puntúa a ninguno.** Hay carga por tutor —porque varios
    llevan dos comisiones y su cola real no está en ninguna fila— pero no hay nota, ni podio, ni
    orden de mejor a peor: las comisiones no son comparables entre sí (tamaño, consigna,
    cohorte), así que un ranking convertiría un hecho en un juicio. Se ordena por la espera más
    antigua, que es lo que dice por dónde empezar.

    La columna **Nota** de la tabla por tutor es lo único que interpreta, y la regla es pareja
    para todos: manda la espera, no el volumen. Sin ella, un tutor con 8 entregas de ayer y otro
    con 5 esperando tres días se leían igual y había que hacer la cuenta a ojo, fila por fila.

    El bloque de desenganche usa el reloj de **la materia**, no el del campus. Con el del campus
    el informe listaba 13 alumnos y los 13 tenían "ingreso al aula: nunca" — o sea encontraba
    sólo a los que no están en ninguna parte. Al que entra a diario para otra materia y hace un
    mes que no abre ésta lo mostraba impecable, y ése es el más recuperable de todos.
    """
    ss = getSampleStyleSheet()
    h0 = ParagraphStyle("h0", parent=ss["Heading1"], fontSize=17, leading=20, textColor=NAVY,
                        spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, textColor=NAVY,
                        spaceBefore=10, spaceAfter=4)
    h3 = ParagraphStyle("h3", parent=ss["Heading3"], fontSize=10, textColor=TEAL,
                        spaceBefore=9, spaceAfter=2)
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=8.5, leading=11.5)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=7, leading=9,
                           textColor=colors.HexColor("#666666"))
    kicker = ParagraphStyle("k", parent=ss["Normal"], fontSize=7.5, leading=9,
                            textColor=TEAL, fontName="Helvetica-Bold")
    alerta = ParagraphStyle("a", parent=ss["Normal"], fontSize=8, leading=11, textColor=RED)
    est_celda = ParagraphStyle("cel", parent=ss["Normal"], fontSize=6.6, leading=7.8)
    est_caja = ParagraphStyle("cj", parent=ss["Normal"], fontSize=8.3, leading=11.5)

    filas = datos.get("filas") or []
    act = datos.get("por_actividad") or []
    tutores = datos.get("por_tutor") or []
    avisos = datos.get("avisos") or []
    des = datos.get("desenganche") or {}
    tot_des = des.get("totales") or {}
    titulo = materia or f"Curso {datos.get('course_id')}"
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"reporte_coordinacion_curso{datos.get('course_id')}"
                        + (f"_{fecha}" if fecha else "") + ".pdf")

    def _suma(clave):
        vals = [f[clave] for f in filas if f.get(clave) is not None]
        return sum(vals) if vals else None

    entregadas, corregidas = _suma("entregados"), _suma("corregidos")
    sin_corregir, sin_nota = _suma("sin_corregir"), _suma("calificado_sin_nota")
    alumnos_tot = _suma("alumnos")
    esperas = [f["espera_max_dias"] for f in filas if f.get("espera_max_dias") is not None]
    espera_max = max(esperas) if esperas else None
    con_tutor = sum(1 for f in filas if f.get("tutor"))
    pct = round(100 * corregidas / entregadas) if entregadas else None

    # ---------------- Página 1: de qué tamaño es cada cosa ----------------
    E = [Paragraph("UTN · TECNICATURA UNIVERSITARIA EN PROGRAMACIÓN", kicker),
         Paragraph(f"Reporte de coordinación — {titulo}", h0),
         Paragraph("Seguimiento de tutores y comisiones · vista de supervisión"
                   + (f" · {fecha}" if fecha else ""), small),
         Spacer(1, 12)]

    pad = datos.get("padron") or {}
    tot_curso = pad.get("total_del_curso")
    kpi_alumnos = (f"{pad.get('en_comisiones')}/{tot_curso}" if tot_curso
                   else _dias_txt(pad.get("en_comisiones") or alumnos_tot))
    E.append(_tiles([
        (f"{con_tutor}/{len(filas)}", "comisiones con tutor", NAVY),
        (f"{pct}%" if pct is not None else "—", "entregas corregidas", TEAL),
        (kpi_alumnos, "alumnos con comisión / total", NAVY),
        (_dias_txt(sin_corregir), "entregas sin corregir", AMBER),
        (tot_des.get("desenganchados", "—"), "no abren la materia", RED),
    ]))
    E.append(Spacer(1, 12))

    if avisos:
        E.append(Paragraph("<b>Atención:</b> este relevamiento tiene huecos. Leelos antes de los números:",
                           alerta))
        for a in avisos[:6]:
            # Los avisos llevan títulos de actividades del campus, y esos vienen con emoji:
            # reportlab los pinta como cuadraditos negros y en un aviso parecen un dato.
            E.append(Paragraph(f"· {sin_emoji(a)}", small))
        E.append(Spacer(1, 8))

    peor = max((f for f in filas if f.get("sin_corregir")),
               key=lambda f: (f.get("espera_max_dias") or 0), default=None)
    resumen = (
        f"<b>{pad.get('en_comisiones') or alumnos_tot or 0}</b> alumnos en {len(filas)} "
        f"comisiones ({con_tutor} con tutor asignado)"
        + (f" sobre <b>{tot_curso}</b> matriculados en el curso"
           + (f", y {pad['sin_comision']} sin comisión asignada" if pad.get("sin_comision")
              else " — cuadra")
           if tot_curso else "")
        + ". "
        + (f"<b>{corregidas} de {entregadas}</b> entregas corregidas ({pct}%), "
           f"<b>{sin_corregir}</b> sin corregir. " if entregadas else
           "No se relevó ninguna entrega. ")
        + (f"La espera más larga es de <b>{_dias_txt(espera_max)} d</b> en "
           f"<b>{peor.get('comision')}</b> "
           f"({(peor.get('tutor') or {}).get('nombre') or 'sin tutor identificado'}). "
           if peor else "")
        + (f"<b>{tot_des['desenganchados']}</b> alumnos no abren {titulo} hace "
           f"{des.get('dias_desenganche')}+ días, y "
           f"<b>{tot_des.get('entran_al_campus_sin_abrir_la_materia')}</b> de ésos sí entran "
           "al campus."
           if tot_des.get("desenganchados") else ""))
    E.append(_caja(resumen, est_caja))
    E.append(Spacer(1, 6))
    E.append(Paragraph(
        "Son hechos del campus, sin veredicto. <b>Espera máx</b> es lo accionable: los días que "
        "aguarda HOY la entrega sin corregir más antigua. <b>Demora</b> es historia: lo que "
        "tardó en corregirse lo que ya tiene nota. <b>Volumen no es atraso</b> — una cola grande "
        "pero de ayer está al día, y una sola entrega de hace tres semanas no lo está.", small))

    focos = focos_de_correccion(datos)
    if focos:
        E.append(Paragraph("Puntos de atención de hoy", h2))
        E.append(_bloque_focos(focos, body, small))

    # ---------------- Página 2: el desglose ----------------
    E.append(PageBreak())
    E.append(Paragraph("Desglose por comisión", h2))
    nohab = datos.get("actividades_no_habilitadas") or 0
    E.append(Paragraph(
        f"Se evalúan las <b>{datos.get('tareas_miradas')} actividades habilitadas</b> a hoy"
        + (f" (de {datos.get('actividades_en_el_curso')} cargadas en el curso): quedan afuera "
           f"{nohab} que todavía no abrieron —parciales, recuperatorios, integradores—, porque "
           "contarlas mostraría entregas en cero sobre algo que el alumno ni ve." if nohab
           else ".")
        + " «Sin corregir» = entregada y todavía sin nota. «Sin nota» = figura corregida pero "
        "sin calificación cargada: no está en ninguna cola, así que nadie la está esperando.",
        small))
    E.append(Spacer(1, 5))
    rows = [["Com.", "Tutor", "Alum.", "Entre-\ngadas", "Corre-\ngidas", "Sin\ncorregir",
             "Sin\nnota", "Espera\nmáx (d)", "Demora\nmed (d)", "Demora\nmáx (d)",
             "Foro\ns/resp."]]
    for f in filas:
        rows.append([f.get("comision"),
                     Paragraph((f.get("tutor") or {}).get("nombre") or "— sin identificar —",
                               est_celda),
                     f.get("alumnos"), _dias_txt(f.get("entregados")),
                     _dias_txt(f.get("corregidos")), _dias_txt(f.get("sin_corregir")),
                     _dias_txt(f.get("calificado_sin_nota")),
                     _dias_txt(f.get("espera_max_dias")),
                     _dias_txt(f.get("demora_mediana_dias")),
                     _dias_txt(f.get("demora_max_dias")),
                     _dias_txt(f.get("consultas_sin_responder"))])
    rows.append(["TOTAL",
                 Paragraph(f"{len(filas)} comisiones · {len(tutores)} tutores", est_celda),
                 _dias_txt(alumnos_tot), _dias_txt(entregadas), _dias_txt(corregidas),
                 _dias_txt(sin_corregir), _dias_txt(sin_nota),
                 _dias_txt(espera_max), "—", "—",
                 _dias_txt(_suma("consultas_sin_responder"))])
    t = _tabla(rows, [1.15, 3.6, 0.95, 1.15, 1.15, 1.15, 1.0, 1.35, 1.35, 1.35, 1.15],
               font=6.6, alinear_der=[2, 3, 4, 5, 6, 7, 8, 9, 10], compacta=True)
    # La fila TOTAL se despega del cuerpo: si se lee como una comisión más, el informe miente.
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dde5ec")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, NAVY)]))
    E.append(t)
    E.append(Paragraph(
        "Las medianas y los máximos no se suman: la fila TOTAL los deja en — a propósito. "
        "«Espera máx» del total es la del curso, no una suma.", small))

    faltan = [f for f in filas if f.get("sin_dato")]
    if faltan:
        E.append(Paragraph("Por qué algunas filas tienen blancos", h3))
        for f in faltan[:10]:
            E.append(Paragraph(f"· <b>{f.get('comision')}</b>: " + " ".join(f["sin_dato"]),
                               small))

    if tutores:
        E.append(Paragraph("Entregas sin corregir, por tutor", h2))
        E.append(Paragraph(
            "Sumando SUS comisiones, porque varios llevan dos y su cola real no está en ninguna "
            "fila de arriba. Ordenado por la espera más antigua: dice por dónde empezar. <b>No "
            "es un ranking ni un puntaje</b> — es carga y cola.", small))
        E.append(Spacer(1, 5))
        rr = [["Tutor", "Comisiones", "Alum.", "Pend.", "Sin\nnota", "Espera\nmáx (d)",
               "Foro\ns/resp.", "Nota"]]
        for t_ in tutores:
            rr.append([Paragraph(t_["tutor"], est_celda),
                       Paragraph(", ".join(c for c in t_["comisiones"] if c), est_celda),
                       t_["alumnos"], t_["sin_corregir"], t_["calificado_sin_nota"],
                       _dias_txt(t_["espera_max_dias"]), t_["consultas_sin_responder"],
                       Paragraph(nota_de_cola(t_["sin_corregir"], t_.get("espera_max_dias")),
                                 est_celda)])
        E.append(_tabla(rr, [3.5, 3.0, 1.0, 1.0, 1.0, 1.35, 1.15, 5.0], font=6.8,
                        alinear_der=[2, 3, 4, 5, 6], compacta=True))
        E.append(Paragraph(
            "La columna <b>Nota</b> lee la cola por la ESPERA, no por el volumen: una cola de 8 "
            "entregas de ayer está al día y una sola de hace tres semanas no. Dice qué hacer con "
            "la cola, no cómo es la persona.", small))

    E.append(Paragraph("Padrón, comisiones y foros", h2))
    for b in [
        f"<b>Comisiones:</b> {con_tutor} de {len(filas)} con tutor asignado "
        f"({len(tutores)} tutores en total).",
        f"<b>Alumnos:</b> {alumnos_tot or 0} sumando las {len(filas)} comisiones. El cuadre "
        "contra el total del curso —y quién quedó sin comisión— va en el informe de nexos, que "
        "es el que lo consulta.",
        f"<b>Actividades:</b> {datos.get('tareas_miradas')} habilitadas y relevadas de "
        f"{datos.get('actividades_en_el_curso')} cargadas en el curso"
        + (f"; {datos.get('actividades_no_habilitadas')} todavía no abrieron"
           if datos.get("actividades_no_habilitadas") else "")
        + (f"; {datos.get('actividades_sin_fecha_de_entrega')} de las habilitadas no tienen "
           "fecha de entrega." if datos.get("actividades_sin_fecha_de_entrega") else "."),
        f"<b>Foros:</b> {_suma('consultas_sin_responder') or 0} consulta(s) de alumnos sin "
        "ninguna respuesta en "
        f"{(datos.get('foros') or {}).get('foros_de_consulta') or 0} foro(s) de consultas"
        + (f". El foro de avisos de la comisión lleva "
           f"{datos['foros']['discusiones_de_avisos']} discusión(es) publicadas."
           if (datos.get("foros") or {}).get("discusiones_de_avisos") else "."),
    ]:
        E.append(Paragraph(f"· {b}", body))

    # ---------------- Página 3: los alumnos que dejaron de entrar ----------------
    E.append(PageBreak())
    E.append(Paragraph(f"Alumnos que dejaron de abrir {titulo}", h2))
    if not des:
        E.append(Paragraph(
            "No se pudo medir el desenganche en esta corrida. NO significa que no haya: "
            "significa que no se sabe.", alerta))
    else:
        todos = [a for a in (des.get("alumnos") or [])
                 if a.get("desenganchado_de_la_materia")]
        # Los que NO están retrasados salen de la lista principal y van a su propio bloque.
        # Son el caso que la columna existe para encontrar —no entra porque ya entregó todo— y
        # mezclados con los otros no aparecían nunca: la tabla se recorta a 25 por urgencia, y
        # justamente éstos no son urgentes. La columna quedaba diciendo "Sí" 25 veces.
        al_dia = [a for a in todos if a.get("retraso") is False]
        lista = [a for a in todos if a.get("retraso") is not False]
        muestra = lista[:_MAX_DESENGANCHE_EN_TABLA]
        E.append(Paragraph(
            f"<b>El reloj es el de esta materia, no el del campus.</b> Son los "
            f"{des.get('dias_desenganche')}+ días sin abrir {titulo} (o no haberla abierto "
            "nunca). Cortar por «días sin entrar al campus» encuentra sólo a los que no aparecen "
            "por ningún lado y pierde a la mayoría: el que entra a diario para otra materia y no "
            "abre ésta se ve impecable con ese corte. Por eso van <b>los dos relojes</b> en cada "
            "fila.", small))
        E.append(Spacer(1, 4))
        if not lista:
            E.append(Paragraph(
                f"Ninguno de los {des.get('relevados')} alumnos relevados dejó de abrir la "
                f"materia por {des.get('dias_desenganche')}+ días.", body))
        else:
            E.append(Paragraph(
                f"<b>{len(lista)} alumnos</b>"
                + (f" (de {len(todos)}; los otros {len(al_dia)} están al día y van aparte "
                   "abajo)" if al_dia else "")
                + ", de los cuales "
                f"{tot_des.get('entran_al_campus_sin_abrir_la_materia')} entran al campus y no "
                "abren la materia — ésos van primero porque no perdieron el acceso: eligieron no "
                "entrar, y son los más recuperables."
                + (f" <b>Se listan los {len(muestra)} más urgentes</b>; el listado completo, con "
                   "el mail de cada uno y el Tutor Nexo de su sede, es el informe de nexos."
                   if len(lista) > len(muestra) else ""), body))
            E.append(Spacer(1, 5))
            ret = des.get("retraso") or {}
            if ret:
                E.append(Paragraph(
                    f"<b>La columna Retraso ({ret['etiqueta']}) separa dos casos que acá se ven "
                    "iguales</b>: el que no entra y además no entregó nada, y el que no entra "
                    "<b>porque ya entregó todo</b>. Al segundo no hay que llamarlo. Hoy son "
                    f"{ret.get('al_dia')} de {ret.get('medidos')}. El rango lo fija el tutor: "
                    "Moodle no expone qué unidad se está cursando, así que sin indicarlo la "
                    "columna no sale — nunca se estima.", small))
                E.append(Spacer(1, 4))
            cab = ["Alumno", "Com.", "Regional", "Último ingreso\nal aula",
                   "Sin abrir\nla materia", "Sin entrar\nal campus", "Situación"]
            anchos_t = [3.9, 1.0, 2.2, 1.9, 1.7, 1.7, 4.6]
            if ret:
                cab.insert(4, f"Retraso\n({ret['etiqueta']})")
                anchos_t = [3.5, 1.0, 1.9, 1.75, 1.35, 1.5, 1.5, 4.5]
            rr = [cab]
            for a in muestra:
                aula = ("Nunca" if a.get("estado_aula") == "nunca_abrio"
                        else f"{a.get('dias_sin_abrir_la_materia')} d")
                camp = ("Nunca" if a.get("dias_sin_entrar_al_campus") is None
                        else f"{a['dias_sin_entrar_al_campus']} d")
                caso = ("entra al campus, no abre la materia"
                        if a.get("entra_al_campus_sin_abrir_la_materia")
                        else "no entra al campus")
                com = "s/com" if a.get("comision") == "(sin comisión)" else a.get("comision")
                fila = [Paragraph(a.get("nombre") or "", est_celda),
                        com or "—",
                        Paragraph(a.get("regional") or "—", est_celda),
                        _fecha_txt(a.get("ultimo_acceso_aula_ts")),
                        aula, camp, Paragraph(caso, est_celda)]
                if ret:
                    # La palabra además del color: el informe se imprime en blanco y negro.
                    r_ = a.get("retraso")
                    txt = ("—" if r_ is None
                           else ('<font color="#a02c2c"><b>Sí</b></font>' if r_
                                 else '<font color="#1f7a6c"><b>No</b></font>'))
                    fila.insert(4, Paragraph(txt, est_celda))
                rr.append(fila)
            E.append(_tabla(rr, anchos_t, font=6.8, compacta=True,
                            alinear_der=([5, 6] if ret else [4, 5])))
            E.append(Paragraph(
                "«Sin entrar al campus» mide la ausencia del <b>sitio completo</b>, no de "
                "esta aula: por eso puede superar los días desde el inicio de cursada. Y "
                "«Nunca» en la columna del aula <b>no es abandono confirmado</b>: puede haberse "
                "matriculado esta semana, y este informe no ve la fecha de matriculación. Sin "
                "mails a propósito — el listado accionable, por sede y con contacto, es el "
                "informe de nexos.", small))

        if al_dia:
            E.append(Paragraph(
                f"No entran, pero están AL DÍA con las entregas ({len(al_dia)})",
                ParagraphStyle("ok", parent=h2, textColor=TEAL)))
            E.append(Paragraph(
                "Entregaron todo lo exigible y por eso no abren la materia. <b>A éstos no hay "
                "que llamarlos</b> — y en la lista de arriba quedaban indistinguibles de los que "
                "abandonaron, que es la razón por la que van aparte.", small))
            E.append(Spacer(1, 4))
            rr2 = [["Alumno", "Com.", "Regional", "Último ingreso\nal aula",
                    "Sin abrir\nla materia", "Sin entrar\nal campus"]]
            for a in al_dia:
                rr2.append([Paragraph(a.get("nombre") or "", est_celda),
                            ("s/com" if a.get("comision") == "(sin comisión)"
                             else a.get("comision")) or "—",
                            Paragraph(a.get("regional") or "—", est_celda),
                            _fecha_txt(a.get("ultimo_acceso_aula_ts")),
                            ("Nunca" if a.get("estado_aula") == "nunca_abrio"
                             else f"{a.get('dias_sin_abrir_la_materia')} d"),
                            ("Nunca" if a.get("dias_sin_entrar_al_campus") is None
                             else f"{a['dias_sin_entrar_al_campus']} d")])
            E.append(_tabla(rr2, [5.0, 1.2, 2.6, 2.4, 2.9, 2.9], font=6.8, compacta=True,
                            alinear_der=[4, 5]))

    # ---------------- Anexo: el detalle largo, sólo si se pide ----------------
    if anexo:
        esp = datos.get("esperando_detalle") or []
        if esp:
            E.append(PageBreak())
            E.append(Paragraph(f"Anexo · Qué está esperando corrección ({len(esp)})", h2))
            E.append(Paragraph(
                "De la espera más vieja a la más nueva. Las tablas de arriba cuentan; ésta dice "
                "<b>cuál</b> y <b>de quién</b>.", small))
            E.append(Spacer(1, 5))
            rr = [["Espera\n(días)", "Com.", "Tutor", "Alumno", "Actividad"]]
            for f in esp:
                rr.append([_dias_txt(f.get("dias_esperando")), f.get("comision"),
                           Paragraph(f.get("tutor") or "—", est_celda),
                           Paragraph(f.get("alumno") or "", est_celda),
                           Paragraph(sin_emoji(f.get("actividad") or ""), est_celda)])
            E.append(_tabla(rr, [1.5, 1.2, 3.6, 4.4, 6.3], alinear_der=[0]))

        sn = datos.get("sin_nota_detalle") or []
        if sn:
            E.append(Paragraph(f"Anexo · Corregidas pero SIN nota cargada ({len(sn)})",
                               ParagraphStyle("snh", parent=h2, textColor=RED)))
            E.append(Paragraph(
                "Moodle las saca de la cola de pendientes porque figuran corregidas, pero no "
                "tienen calificación. <b>Nadie las está esperando</b> — no aparecen en ningún "
                "listado, ni del tutor ni del alumno.", small))
            E.append(Spacer(1, 5))
            rr = [["Desde la\nentrega (d)", "Com.", "Tutor", "Alumno", "Actividad"]]
            for f in sn:
                rr.append([_dias_txt(f.get("dias_desde_la_entrega")), f.get("comision"),
                           Paragraph(f.get("tutor") or "—", est_celda),
                           Paragraph(f.get("alumno") or "", est_celda),
                           Paragraph(sin_emoji(f.get("actividad") or ""), est_celda)])
            E.append(_tabla(rr, [1.7, 1.2, 3.5, 4.3, 6.3], alinear_der=[0]))

        hilos = datos.get("consultas_sin_responder_detalle") or []
        if hilos:
            E.append(Paragraph(f"Anexo · Consultas de foro sin ninguna respuesta ({len(hilos)})",
                               h2))
            E.append(Paragraph(
                "Hilos abiertos por un alumno donde <b>nadie</b> contestó. El campus no dice "
                "quién debía responder, así que atribuirle el silencio a alguien sería deducirlo "
                "del nombre. La comisión sale de quién preguntó.", small))
            E.append(Spacer(1, 5))
            rr = [["Sin respuesta\n(días)", "Com.", "Alumno", "Consulta", "Foro"]]
            for h_ in hilos:
                rr.append([_dias_txt(h_.get("dias_esperando")), h_.get("comision"),
                           Paragraph(h_.get("alumno") or "", est_celda),
                           Paragraph(sin_emoji(h_.get("titulo") or ""), est_celda),
                           Paragraph(sin_emoji(h_.get("foro") or ""), est_celda)])
            E.append(_tabla(rr, [1.9, 1.2, 4.0, 5.6, 4.3], alinear_der=[0]))

        if act:
            E.append(PageBreak())
            E.append(Paragraph("Anexo · Por actividad", h2))
            E.append(Paragraph(
                "La misma información cortada al revés. Cuando una actividad se atrasa en varias "
                "comisiones a la vez, el problema suele ser de la consigna o del calendario y no "
                "de una persona — eso por comisión no se ve. <b>Sin fecha</b> significa que la "
                "actividad no tiene `duedate`, y ahí «no entregó» no se distingue de «todavía no "
                "vencía».", small))
            E.append(Spacer(1, 5))
            rr = [["Actividad", "Venci-\nmiento", "Entre-\ngadas", "Corre-\ngidas",
                   "Sin\ncorregir", "Comis. con\ncola", "Espera\nmáx (d)"]]
            for a in act:
                rr.append([Paragraph(sin_emoji(a["titulo"]), est_celda), a["vencimiento"],
                           a["entregadas"], a["corregidas"], a["sin_corregir"],
                           a["comisiones_con_cola"], _dias_txt(a.get("espera_max_dias"))])
            E.append(_tabla(rr, [6.9, 1.7, 1.5, 1.5, 1.5, 2.0, 1.9],
                            alinear_der=[2, 3, 4, 5, 6]))

    E.append(Spacer(1, 10))
    E.append(Paragraph(
        f"UTN · {titulo} · datos leídos EN VIVO de la API REST del campus"
        + (f" el {fecha}" if fecha else "")
        + f" · relevamiento en {datos.get('segundos')} s · hechos por comisión, sin ranking de "
        "personas."
        + ("" if anexo else " El detalle entrega por entrega sale con anexo=True."), small))

    def _pie(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.2)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(2.0 * cm, 0.8 * cm, f"Reporte de coordinación — {titulo}")
        canvas.drawRightString(19.0 * cm, 0.8 * cm, f"Pág. {doc_.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.5 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Reporte de coordinación — {titulo}",
                            author="tup-campus-navigator")
    doc.build(E, onFirstPage=_pie, onLaterPages=_pie)
    listados = [a for a in (des.get("alumnos") or []) if a.get("desenganchado_de_la_materia")]
    return {"archivo": path, "paginas": getattr(doc, "page", None),
            "incluye_emails": False, "con_anexo": bool(anexo),
            "desenganchados_listados": min(len(listados), _MAX_DESENGANCHE_EN_TABLA),
            "desenganchados_total": tot_des.get("desenganchados"),
            "degradado": bool(avisos)}


def _bloque_focos(focos: list[tuple], body, small, ancho_total: float = 17.0) -> Table:
    """El bloque numerado de "Focos de hoy". Lo usan los tres informes, con el mismo formato."""
    est_t = ParagraphStyle("pt", parent=body, fontName="Helvetica-Bold", fontSize=8.5)
    colores = [RED, AMBER, NAVY, TEAL]
    rows = []
    for i, (tp, det) in enumerate(focos):
        num = Paragraph(f'<font color="white"><b>{i + 1}</b></font>',
                        ParagraphStyle("n", parent=body, alignment=1, fontSize=11))
        cuerpo = Table([[Paragraph(tp, est_t)], [Paragraph(det, small)]],
                       style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 6),
                                         ("TOPPADDING", (0, 0), (-1, -1), 1),
                                         ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        rows.append([num, cuerpo])
    t = Table(rows, colWidths=[0.9 * cm, (ancho_total - 0.9) * cm])
    est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
           ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, LT])]
    for i in range(len(rows)):
        est.append(("BACKGROUND", (0, i), (0, i), colores[i % len(colores)]))
    t.setStyle(TableStyle(est))
    return t


# ---------------------------------------------------------------------------
# PDF POR COMISIÓN — render PURO del dict de `calificador.informe`
# ---------------------------------------------------------------------------
#
# Un documento por TUTOR y no uno del curso, y la razón es de destinatario, no de
# formato: es el informe que se le manda a cada tutor con sus alumnos, y un PDF donde
# su comisión aparece al lado de las otras catorce se lee como una comparación entre
# personas. La misma separación que ya hay entre `informes_nexos` (alumnos, va a los
# nexos) y `reporte_coordinacion` (trabajo docente, va a coordinación).
#
# Va APAISADO porque el contenido lo pide: la matriz de videos son 29 columnas. En A4
# vertical no entran, y la alternativa —resumirla en un promedio— es justamente lo que
# este proyecto ya probó y descartó tres veces. Una matriz no resume: MUESTRA. Deja ver
# de un vistazo lo que ningún agregado deja: la unidad que está vacía en toda la
# comisión, el alumno que hizo la 4 sin haber hecho la 1, el que arrancó y paró.

_TIPO_TITULO = {"video": "Videos", "leccion": "Lecciones",
                "autoevaluacion": "Autoevaluaciones", "entrega": "Entregas"}
# Cuántos alumnos entran por página en la matriz antes de partirla a mano. reportlab
# parte solo, pero acá se parte con título propio para que la página 2 diga a qué tanda
# de alumnos corresponde: 29 columnas de números sin ese rótulo no se leen. Con 34 la
# tanda desbordaba UNA fila y esa fila caía sola en una página sin título ni leyenda —
# se ve como una tabla huérfana. 30 entra con aire.
_ALUMNOS_POR_PAGINA = 30


def _nota_celda(n) -> str:
    """La nota como se escribe en una celda: coma decimal y sin ceros de más. PURA.

    `None` -> cadena vacía a propósito. Un cero y un "no la hizo" son cosas distintas y
    la celda vacía es la única forma de que no se confundan de un vistazo.
    """
    if n is None:
        return ""
    return f"{round(float(n), 1):g}".replace(".", ",")


def _acceso_txt(fila: dict) -> str:
    """Última vez que el alumno abrió LA MATERIA, como se muestra. PURA.

    Los tres estados salen distintos a propósito: "Nunca abrió" es un hecho del campus,
    "sin dato" es que no se pudo leer, y confundirlos manda a llamar a alguien por algo
    que no pasó. Va la fecha además de los días porque son para cosas distintas: los días
    ordenan, la fecha es lo que se le nombra al alumno cuando se lo contacta.
    """
    estado = fila.get("estado_aula")
    if estado == "sin_dato":
        return "sin dato"
    if estado == "nunca_abrio":
        return "Nunca abrió"
    d = fila.get("dias_sin_abrir_la_materia")
    dias = "hoy" if d == 0 else f"hace {d} d"
    return f"{dias} - {_fecha_txt(fila.get('ultimo_acceso_aula_ts'))}"


def _matriz_de_tipo(catalogo: dict, tipo: str, alumnos: list[dict],
                    ancho_total: float):
    """Matriz alumnos x actividades de UN tipo. -> (tabla, leyenda) o (None, []).

    Las columnas se numeran y el título completo va en la leyenda de abajo. Ponerlo en la
    cabecera obligaba a rotarlo o a truncarlo a cuatro letras, y "Video 1 Semana 1 SN" y
    "Video 1 Semana 2 SN" truncados son la misma columna.
    """
    items = [it for it in catalogo["items"] if it["tipo"] == tipo]
    if not items:
        return None, []

    # Cabecera de dos filas: la unidad arriba (con SPAN) y el número de columna abajo.
    fila_u, fila_n, spans, ini = [""], ["Alumno"], [], 1
    ult = object()
    for i, it in enumerate(items):
        u = it["unidad"]
        if u != ult:
            if i:
                spans.append((ini, i))
            ini, ult = i + 1, u
            fila_u.append(f"U{u}" if u is not None else "s/u")
        else:
            fila_u.append("")
        fila_n.append(str(it["nro"]))
    spans.append((ini, len(items)))

    filas = [fila_u, fila_n]
    for a in alumnos:
        fila = [sin_emoji(a["nombre"])[:30]]
        for it in items:
            n = a["notas"].get(it["cmid"])
            fila.append(_nota_celda(n["nota"]) if n else "")
        filas.append(fila)

    ancho_nombre = 4.6
    ancho_col = max((ancho_total - ancho_nombre) / len(items), 0.42)
    t = Table(filas, colWidths=[ancho_nombre * cm] + [ancho_col * cm] * len(items),
              repeatRows=2)
    est = [("BACKGROUND", (0, 0), (-1, 1), NAVY),
           ("TEXTCOLOR", (0, 0), (-1, 1), colors.white),
           ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
           ("FONTSIZE", (0, 0), (-1, -1), 5.6),
           ("ALIGN", (1, 0), (-1, -1), "CENTER"),
           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("GRID", (0, 0), (-1, -1), 0.25, GREY),
           ("ROWBACKGROUNDS", (0, 2), (-1, -1), [colors.white, LT]),
           ("TOPPADDING", (0, 0), (-1, -1), 1.2),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]
    for a, b in spans:
        if b > a:
            est.append(("SPAN", (a, 0), (b, 0)))
    t.setStyle(TableStyle(est))

    leyenda = [f"{it['nro']} = {sin_emoji(it['titulo'])}" for it in items]
    return t, leyenda


def informe_comision_pdf(bloque: dict, catalogo: dict, dest_dir: str,
                         materia: str = "", fecha: str = "",
                         tipos: tuple = ("video", "leccion", "autoevaluacion")) -> dict:
    """PDF de UNA comisión: su tutor, sus alumnos, qué hizo cada uno y con qué nota.

    PURO en lo que importa: recibe el dict ya armado por `calificador.informe`, no el
    cliente. Se puede testear sin red y sin credenciales, que es donde este proyecto
    encuentra los bugs.
    """
    os.makedirs(dest_dir, exist_ok=True)
    com = bloque.get("comision") or "comision"
    path = os.path.join(dest_dir, f"informe_{com}_curso{bloque.get('course_id', '')}.pdf")

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1c", parent=ss["Heading1"], fontSize=14, leading=16,
                        textColor=NAVY, spaceAfter=2)
    h2 = ParagraphStyle("h2c", parent=ss["Heading2"], fontSize=10, leading=12,
                        textColor=colors.HexColor("#1c5a7a"), spaceBefore=8, spaceAfter=3)
    small = ParagraphStyle("sc", parent=ss["Normal"], fontSize=7, leading=9,
                           textColor=colors.grey)
    mini = ParagraphStyle("mc", parent=ss["Normal"], fontSize=5.8, leading=7.2,
                          textColor=colors.HexColor("#555555"))

    ancho_total = 27.0
    doc = SimpleDocTemplate(path, pagesize=landscape(A4),
                            leftMargin=1.1 * cm, rightMargin=1.1 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.0 * cm)

    alumnos = bloque.get("alumnos") or []
    res = bloque.get("resumen") or {}
    tutor = (bloque.get("tutor") or {}).get("nombre") or "SIN DOCENTE ASIGNADO"
    nombre_campus = bloque.get("nombre") or com

    E = [Paragraph(f"{sin_emoji(materia) or 'Curso'} - {com.upper()} ({nombre_campus})", h1),
         Paragraph(f"Tutor/a: <b>{sin_emoji(tutor)}</b> &nbsp;·&nbsp; {len(alumnos)} alumnos"
                   + (f" &nbsp;·&nbsp; {fecha}" if fecha else ""), small),
         Spacer(1, 6)]

    if bloque.get("error"):
        E.append(Paragraph(f"<b>No se pudo leer esta comisión:</b> {bloque['error']}", small))
        doc.build(E)
        return {"ok": False, "archivo": path, "error": bloque["error"]}

    E.append(_tiles([
        (res.get("alumnos", 0), "ALUMNOS", NAVY),
        (res.get("con_actividad", 0), "CON ACTIVIDAD REGISTRADA", TEAL),
        (res.get("sin_actividad", 0), "SIN NINGUNA ACTIVIDAD", AMBER),
        (res.get("nunca_abrieron_la_materia", 0), "NUNCA ABRIERON LA MATERIA", RED),
    ], ancho_total))
    E.append(Spacer(1, 8))

    # --- Panorama: una fila por alumno ---
    unidades = catalogo.get("unidades") or []
    cab = (["Alumno"] + [f"U{u}" for u in unidades]
           + ["Videos", "Lecc.", "Autoev.", "Última vez que abrió la materia"])
    filas = [cab]
    # La celda por unidad cuenta EXACTAMENTE los tipos que muestra el informe. Sumar
    # también las entregas metía en el denominador 15 tareas que en Matemática no tienen
    # una sola entrega en todo el curso: el alumno que hizo todo lo que podía hacer salía
    # "7/9" y se leía como si le faltara algo. Un denominador inalcanzable no mide nada.
    for a in alumnos:
        f = [sin_emoji(a["nombre"])[:34]]
        for u in unidades:
            b = a["por_unidad"].get(u) or {}
            hechas = sum(b.get(t, {}).get("hechas", 0) for t in tipos)
            total = sum(b.get(t, {}).get("total", 0) for t in tipos)
            f.append(f"{hechas}/{total}" if total else "-")
        for t in ("video", "leccion", "autoevaluacion"):
            pt = a["por_tipo"][t]
            f.append(f"{pt['hechas']}/{pt['total']}")
        f.append(_acceso_txt(a))
        filas.append(f)

    anchos = ([6.4] + [1.05] * len(unidades) + [1.35, 1.15, 1.35]
              + [ancho_total - 6.4 - 1.05 * len(unidades) - 3.85])
    E.append(Paragraph("Panorama de la comisión — actividades hechas sobre el total del curso", h2))
    E.append(_tabla(filas, anchos, font=6.4, compacta=True))
    E.append(Paragraph(
        "«hechas/total» cuenta videos, lecciones y autoevaluaciones con nota cargada. Un «0/12» "
        "es que no hizo ninguna, NO que las hizo mal. La columna de acceso usa el reloj de ESTA "
        "materia y no el del campus: quien entra todos los días para otra materia y no abre ésta "
        "figura al día si se mira el reloj equivocado.", mini))
    n_entregas = (res.get("actividades_del_curso") or {}).get("entrega") or 0
    if n_entregas and not (res.get("notas_cargadas") or {}).get("entrega"):
        # No es una omisión: es el hallazgo que motivó todo este informe. Decirlo acá
        # evita que alguien lea el documento y concluya que las entregas no se miraron.
        E.append(Paragraph(
            f"Las {n_entregas} tareas de ENTREGA del aula quedan fuera de este informe "
            "porque no tienen una sola entrega registrada en TODO el curso — no es que "
            "no se miraron. La cursada de esta materia pasa por los videos, las "
            "lecciones y las autoevaluaciones.", mini))

    # --- Una matriz por tipo de actividad ---
    for tipo in tipos:
        for i in range(0, max(len(alumnos), 1), _ALUMNOS_POR_PAGINA):
            tanda = alumnos[i:i + _ALUMNOS_POR_PAGINA]
            if not tanda:
                break
            t, leyenda = _matriz_de_tipo(catalogo, tipo, tanda, ancho_total)
            if t is None:
                break
            E.append(PageBreak())
            sufijo = ("" if len(alumnos) <= _ALUMNOS_POR_PAGINA else
                      f" - alumnos {i + 1} a {min(i + _ALUMNOS_POR_PAGINA, len(alumnos))}")
            E.append(Paragraph(f"{_TIPO_TITULO.get(tipo, tipo)}: la nota de cada alumno, "
                               f"actividad por actividad{sufijo}", h2))
            E.append(t)
            E.append(Spacer(1, 4))
            E.append(Paragraph("<b>Columnas:</b> " + " &nbsp;·&nbsp; ".join(leyenda), mini))
            E.append(Paragraph(
                "Celda vacía = sin nota cargada. La nota va en la escala de SU actividad: los "
                "videos y las autoevaluaciones son sobre 10, las lecciones sobre 100 salvo donde "
                "el aula diga otra cosa.", mini))

    avisos = list(bloque.get("avisos") or [])
    if avisos:
        E.append(PageBreak())
        E.append(Paragraph("Lo que este informe NO pudo relevar", h2))
        for a in avisos:
            E.append(Paragraph(f"- {sin_emoji(a)}", small))

    doc.build(E)
    return {"ok": True, "archivo": path, "alumnos": len(alumnos),
            "comision": com, "tutor": tutor}
