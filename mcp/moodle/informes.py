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
coordinación (`panorama_comisiones`) — y separarlos es lo que deja hablar de cada cosa sin ruido.
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
    `panorama_comisiones` y va a coordinación. Estaban juntos en un mismo documento y el
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
        (datos.get("regionales", "—"), "regionales", TEAL),
    ]))
    E.append(Spacer(1, 12))

    if meta.get("degradado"):
        E.append(Paragraph("⚠️ Este relevamiento está INCOMPLETO. Los números de arriba no "
                           "cubren todo el curso:", alerta))
        for s in meta.get("sin_dato", [])[:6]:
            E.append(Paragraph(f"· {s}", small))
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
    E.append(Paragraph(
        "Este informe habla de <b>alumnos</b> y de nadie más: no trae ninguna medición del "
        "trabajo de los tutores.", small))

    focos = focos_de_alumnos(datos)
    if focos:
        E.append(Paragraph("Focos de hoy", h2))
        est_t = ParagraphStyle("pt", parent=body, fontName="Helvetica-Bold", fontSize=8.5)
        colores = [RED, AMBER, NAVY, TEAL]
        rows = []
        for i, (titulo_p, detalle) in enumerate(focos):
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

        cab = ["Alumno", "Com.", "Situación", "Sin abrir\nla materia",
               "Sin entrar\nal campus"]
        anchos = [6.0, 1.3, 4.7, 2.5, 2.5]
        if emails:
            cab.insert(1, "Email")
            anchos = [4.0, 4.4, 1.15, 4.0, 1.75, 1.7]
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
        + ("" if pad.get("cuadra") else " ⚠️ La cuenta NO cierra: puede haber alguien "
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
            "⚠️ Este documento incluye datos de contacto de alumnos (mails personales). No lo "
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
        canvas.drawString(
            2.0 * cm, 0.85 * cm,
            "Situación: «entra al campus, no abre la materia» = eligió no entrar, es el más "
            "recuperable · «no entra al campus» = tampoco aparece por Moodle · «sin dato» = no "
            "se pudo medir, NO es que no la haya abierto.")
        canvas.drawRightString(19.0 * cm, 0.85 * cm, f"pág. {doc_.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.7 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Seguimiento de alumnos — {titulo}",
                            author="tup-campus-navigator")
    doc.build(E, onFirstPage=_pie, onLaterPages=_pie)
    return {"archivo": path, "paginas": getattr(doc, "page", None),
            "incluye_emails": bool(emails), "degradado": bool(meta.get("degradado"))}


# ---------------------------------------------------------------------------
# PDF del PANORAMA — trabajo de corrección. Render PURO.
# ---------------------------------------------------------------------------

def _dias_txt(d) -> str:
    return "—" if d is None else (f"{d:g}" if isinstance(d, float) else str(d))


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


def focos_de_correccion(datos: dict, tope: int = 4) -> list[tuple]:
    """PURA: los focos del trabajo de corrección. -> [(titulo, detalle)].

    Hechos con su cuenta al lado y a quién llamar. Sin adjetivos y sin puntaje: la unidad de
    medida es la comisión o la actividad, nunca la persona.
    """
    filas = datos.get("filas") or []
    act = datos.get("por_actividad") or []
    puntos: list[tuple] = []

    con_cola = [f for f in filas if f.get("sin_corregir")]
    if con_cola:
        peor = max(con_cola, key=lambda f: (f.get("espera_max_dias") or 0))
        total = sum(f["sin_corregir"] for f in con_cola)
        quien = (peor.get("tutor") or {}).get("nombre") or "sin tutor identificado"
        puntos.append((
            f"{total} entregas sin corregir en {len(con_cola)} comisión(es)",
            f"La más antigua espera hace {_dias_txt(peor.get('espera_max_dias'))} día(s), en "
            f"{peor.get('comision')} ({quien}). Volumen no es atraso: una cola grande pero de "
            "ayer está al día — lo accionable es la espera, no el conteo."))
    elif datos.get("tareas_miradas"):
        puntos.append((
            "No hay entregas esperando corrección",
            f"Está corregido todo lo entregado en las {datos['tareas_miradas']} actividades "
            "miradas. Es sobre esas actividades: mirá los huecos si hay alguno declarado."))

    act_cola = [a for a in act if a.get("sin_corregir")]
    if act_cola:
        # `por_actividad` viene ordenado por ESPERA, no por volumen: el título tiene que decir
        # eso. Decía "la actividad con más cola" y mostraba una de 1 sola entrega — la etiqueta
        # contradecía al dato, que es la forma más barata de que nadie le crea al informe.
        a0 = act_cola[0]
        varias = (a0.get("comisiones_con_cola") or 0) >= 2
        puntos.append((
            f"La actividad que más espera: {sin_emoji(a0['titulo'])[:60]}",
            f"{a0['sin_corregir']} sin corregir en {a0['comisiones_con_cola']} comisión(es), la "
            f"más vieja esperando {_dias_txt(a0.get('espera_max_dias'))} día(s)."
            + (" Cuando una actividad se atrasa en varias comisiones a la vez, suele ser de la "
               "consigna o del calendario y no de una persona." if varias else "")
            + (f" La de más volumen es otra: {sin_emoji(max(act_cola, key=lambda a: a['sin_corregir'])['titulo'])[:50]}"
               f" ({max(a['sin_corregir'] for a in act_cola)} en cola)."
               if max(act_cola, key=lambda a: a["sin_corregir"]) is not a0 else "")))

    sin_nota = sum(f.get("calificado_sin_nota") or 0 for f in filas)
    if sin_nota:
        puntos.append((
            f"{sin_nota} entregas figuran corregidas pero SIN nota",
            "Ni pendientes ni calificadas: no salen en ninguna cola, así que nadie las está "
            "esperando. Hay que cargarles la nota a mano."))

    sf = datos.get("actividades_sin_fecha_de_entrega") or 0
    if sf:
        puntos.append((
            f"{sf} de {len(act)} actividades no tienen fecha de entrega",
            "Sin `duedate` no se puede distinguir «no entregó» de «todavía no vencía». Cuidado "
            "al leer cualquier conteo de faltantes como abandono: es lo que hace que la lista "
            "de riesgo marque al padrón entero."))

    return puntos[:tope]


def panorama_pdf(datos: dict, dest_dir: str, materia: str = "", fecha: str = "") -> dict:
    """Renderiza el panorama del curso (trabajo de corrección). PURA.

    Es el informe de COORDINACIÓN sobre el trabajo docente, y el complemento del de nexos
    (`informe_nexos_pdf`), que habla sólo de alumnos. Están separados a propósito: juntos, un
    tutor leía la lista de alumnos como parte de su evaluación.

    **Nombra a todos los tutores y no puntúa a ninguno.** Hay carga por tutor —porque seis
    llevan dos comisiones y su cola real no está en ninguna fila— pero no hay nota, ni podio,
    ni etiqueta sobre la persona: las comisiones no son comparables entre sí (tamaño, consigna,
    cohorte), así que un ranking convertiría un hecho en un juicio. Se ordena por la espera más
    antigua, que es lo que dice por dónde empezar.

    Tampoco emite veredicto: hechos y huecos, la conclusión la saca quien coordina.
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
    est_celda = ParagraphStyle("cel", parent=ss["Normal"], fontSize=7, leading=8.2)

    filas = datos.get("filas") or []
    act = datos.get("por_actividad") or []
    tutores = datos.get("por_tutor") or []
    avisos = datos.get("avisos") or []
    titulo = materia or f"Curso {datos.get('course_id')}"
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"panorama_curso{datos.get('course_id')}"
                        + (f"_{fecha}" if fecha else "") + ".pdf")

    def _suma(clave):
        vals = [f[clave] for f in filas if f.get(clave) is not None]
        return sum(vals) if vals else None

    entregadas, corregidas = _suma("entregados"), _suma("corregidos")
    sin_corregir = _suma("sin_corregir")
    esperas = [f["espera_max_dias"] for f in filas if f.get("espera_max_dias") is not None]
    con_tutor = sum(1 for f in filas if f.get("tutor"))
    pct = round(100 * corregidas / entregadas) if entregadas else None

    E = [Paragraph("UTN · TECNICATURA UNIVERSITARIA EN PROGRAMACIÓN", kicker),
         Paragraph(f"Panorama del curso — {titulo}", h0),
         Paragraph("Trabajo de corrección por comisión · vista de coordinación"
                   + (f" · {fecha}" if fecha else ""), small),
         Spacer(1, 12)]

    E.append(_tiles([
        (f"{con_tutor}/{len(filas)}", "comisiones con tutor", NAVY),
        (f"{pct}%" if pct is not None else "—", "entregas corregidas", TEAL),
        (_dias_txt(entregadas), "entregas relevadas", NAVY),
        (_dias_txt(sin_corregir), "sin corregir", AMBER),
        (_dias_txt(max(esperas) if esperas else None), "espera máx (días)", RED),
    ]))
    E.append(Spacer(1, 12))

    if avisos:
        E.append(Paragraph("⚠️ Este relevamiento tiene huecos. Leelos antes de los números:",
                           alerta))
        for a in avisos[:6]:
            E.append(Paragraph(f"· {a}", small))
        E.append(Spacer(1, 8))

    E.append(Paragraph("Cómo leer esto", h2))
    E.append(Paragraph(
        "<b>Espera máx</b> es lo accionable: los días que aguarda HOY la entrega sin corregir "
        "más antigua. <b>Demora med</b> es historia: lo que tardó en corregirse lo que ya se "
        "corrigió. <b>Volumen no es atraso</b> — una cola grande pero de ayer está al día, y "
        "una cola de una sola entrega de hace tres semanas no lo está.", body))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        "Las filas son <b>hechos por comisión</b>. Se nombra al tutor a cargo porque sin eso el "
        "informe no sirve para saber a quién llamar — <b>no hay puntaje ni ranking de "
        "personas</b>, y no lo armes al presentarlo: las comisiones no son comparables entre sí "
        "(distinto tamaño, distinta consigna, distinta cohorte). Un número alto puede ser una "
        "semana de parcial. Y un 0 con motivo en <b>sin_dato</b> no es un 0 de trabajo "
        "terminado.", small))

    focos = focos_de_correccion(datos)
    if focos:
        E.append(Paragraph("Focos de hoy", h2))
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
        t = Table(rows, colWidths=[0.9 * cm, 16.1 * cm])
        est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
               ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, LT])]
        for i in range(len(rows)):
            est.append(("BACKGROUND", (0, i), (0, i), colores[i % len(colores)]))
        t.setStyle(TableStyle(est))
        E.append(t)

    # ---- Por comisión ----
    E.append(PageBreak())
    E.append(Paragraph("Por comisión", h2))
    E.append(Paragraph(
        f"{datos.get('tareas_miradas')} de {datos.get('tareas_pedidas')} actividades relevadas. "
        "«Sin corregir» = entregada y todavía sin nota. «Sin nota» = figura corregida pero sin "
        "calificación cargada: nadie la está esperando.", small))
    E.append(Spacer(1, 5))
    rows = [["Com.", "Tutor", "Alum.", "Entre-\ngadas", "Corre-\ngidas", "Sin\ncorregir",
             "Sin\nnota", "Espera\nmáx (d)", "Demora\nmed (d)", "Foro\ns/resp."]]
    for f in filas:
        rows.append([f.get("comision"),
                     Paragraph((f.get("tutor") or {}).get("nombre") or "— sin identificar —",
                               est_celda),
                     f.get("alumnos"), _dias_txt(f.get("entregados")),
                     _dias_txt(f.get("corregidos")), _dias_txt(f.get("sin_corregir")),
                     _dias_txt(f.get("calificado_sin_nota")),
                     _dias_txt(f.get("espera_max_dias")),
                     _dias_txt(f.get("demora_mediana_dias")),
                     _dias_txt(f.get("consultas_sin_responder"))])
    E.append(_tabla(rows, [1.25, 4.15, 1.1, 1.3, 1.3, 1.2, 1.1, 1.4, 1.4, 1.25],
                    alinear_der=[2, 3, 4, 5, 6, 7, 8, 9]))

    faltan = [f for f in filas if f.get("sin_dato")]
    if faltan:
        E.append(Paragraph("Por qué algunas filas tienen blancos", h3))
        for f in faltan[:10]:
            E.append(Paragraph(f"· <b>{f.get('comision')}</b>: " + " ".join(f["sin_dato"]),
                               small))

    # ---- Por tutor ----
    if tutores:
        E.append(Paragraph("Carga por tutor", h2))
        E.append(Paragraph(
            "Sumando SUS comisiones, porque varios llevan dos y su cola real no está en ninguna "
            "fila de arriba. Ordenado por la espera más antigua: dice por dónde empezar. "
            "<b>No es un ranking ni un puntaje</b> — es carga y cola.", small))
        E.append(Spacer(1, 5))
        rr = [["Tutor", "Comisiones", "Alum.", "Sin\ncorregir", "Sin\nnota",
               "Espera\nmáx (d)", "Foro\ns/resp."]]
        for t_ in tutores:
            rr.append([Paragraph(t_["tutor"], est_celda),
                       Paragraph(", ".join(c for c in t_["comisiones"] if c), est_celda),
                       t_["alumnos"], t_["sin_corregir"], t_["calificado_sin_nota"],
                       _dias_txt(t_["espera_max_dias"]), t_["consultas_sin_responder"]])
        E.append(_tabla(rr, [4.6, 4.4, 1.4, 1.7, 1.4, 1.9, 1.6],
                        alinear_der=[2, 3, 4, 5, 6]))

    # ---- Por actividad ----
    if act:
        E.append(PageBreak())
        E.append(Paragraph("Por actividad", h2))
        E.append(Paragraph(
            "La misma información cortada al revés. Cuando una actividad se atrasa en varias "
            "comisiones a la vez, el problema suele ser de la consigna o del calendario y no de "
            "una persona — eso por comisión no se ve. «Vencimiento» sale de la fecha de entrega "
            "de Moodle: <b>sin fecha</b> significa que la actividad no tiene `duedate`, y ahí "
            "«no entregó» no se distingue de «todavía no vencía».", small))
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
        "personas. Los alumnos que dejaron de abrir la materia van en el informe de nexos.",
        small))

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Panorama del curso — {titulo}",
                            author="tup-campus-navigator")
    doc.build(E)
    return {"archivo": path, "paginas": getattr(doc, "page", None),
            "incluye_emails": False, "degradado": bool(avisos)}


# ---------------------------------------------------------------------------
# PDF de AVANCE — cómo van los alumnos con las entregas. Render PURO.
# ---------------------------------------------------------------------------

def focos_de_avance(datos: dict, tope: int = 4) -> list[tuple]:
    """PURA: los focos del avance. Hechos con su cuenta, sin adjetivos ni porcentajes."""
    coms = datos.get("por_comision") or []
    puntos: list[tuple] = []

    cero = datos.get("alumnos_en_cero") or 0
    if cero:
        top = sorted(coms, key=lambda c: -(c["alumnos_en_cero"] or 0))[:3]
        puntos.append((
            f"{cero} de {datos.get('alumnos')} alumnos no entregaron NADA",
            "Donde más se concentra: "
            + ", ".join(f"{c['comision']} ({c['alumnos_en_cero']} de {c['alumnos']})"
                        for c in top if c["alumnos_en_cero"])
            + ". Ojo: quien se matriculó tarde también aparece acá, y la API no expone la fecha "
              "de matriculación."))

    atrasadas = [c for c in coms if c.get("atrasada")]
    if atrasadas:
        se_fueron = [c for c in atrasadas if "no abren la materia" in (c.get("lectura") or "")]
        estan = [c for c in atrasadas if c not in se_fueron]
        detalle = []
        if estan:
            detalle.append("con los alumnos entrando (entregan poco, no es que se fueron): "
                           + ", ".join(c["comision"] for c in estan))
        if se_fueron:
            detalle.append("y por alumnos que dejaron de abrir la materia: "
                           + ", ".join(c["comision"] for c in se_fueron))
        puntos.append((
            f"{len(atrasadas)} comisión(es) por debajo de la mediana del curso",
            "Son dos problemas distintos y se resuelven distinto — " + " · ".join(detalle)
            + f". La mediana del curso es {datos.get('entregas_mediana_curso')} entrega(s)."))

    esp = datos.get("esperando_correccion") or 0
    if esp:
        peor = max((f for f in datos.get("flojos") or []
                    if f.get("espera_correccion_dias")),
                   key=lambda f: f["espera_correccion_dias"], default=None)
        puntos.append((
            f"{esp} alumnos entregaron y siguen esperando la corrección",
            "Es el único renglón donde el problema NO es del alumno: entregó y no recibió "
            "nada. La espera más larga es de "
            f"{_dias_txt(peor['espera_correccion_dias']) if peor else '—'} día(s)"
            + (f", en {peor['comision']}." if peor else ".")))

    desap = datos.get("con_desaprobadas") or 0
    if desap:
        puntos.append((
            f"{desap} alumnos tienen al menos una actividad desaprobada",
            "La nota se lee por el texto de la escala, no por el número: en este campus la "
            "escala de TPs va invertida y un índice suelto se leería al revés."))

    return puntos[:tope]


def avance_pdf(datos: dict, dest_dir: str, materia: str = "", fecha: str = "",
               emails: bool = True) -> dict:
    """Renderiza el informe de avance de alumnos. PURA.

    La tercera vista del curso: `informes_nexos` dice si el alumno APARECE,
    `panorama_comisiones` si el tutor CORRIGIÓ, y ésta si el alumno ENTREGA y aprueba.

    **No hay porcentaje de avance y es deliberado**: sin fechas de entrega no existe un
    denominador honesto, así que cada alumno va contra la mediana de su comisión y cada
    comisión contra la del curso. Un "35% del plan" sería inventado.
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
    est_celda = ParagraphStyle("cel", parent=ss["Normal"], fontSize=7, leading=8.2)

    meta = datos.get("_meta", {})
    coms = datos.get("por_comision") or []
    flojos = datos.get("flojos") or []
    titulo = materia or f"Curso {datos.get('course_id')}"
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"avance_curso{datos.get('course_id')}"
                        + (f"_{fecha}" if fecha else "") + ".pdf")

    E = [Paragraph("UTN · TECNICATURA UNIVERSITARIA EN PROGRAMACIÓN", kicker),
         Paragraph(f"Avance de los alumnos — {titulo}", h0),
         Paragraph("Entregas y notas por comisión · vista de coordinación"
                   + (f" · {fecha}" if fecha else ""), small),
         Spacer(1, 12)]

    E.append(_tiles([
        (datos.get("alumnos", "—"), "alumnos", NAVY),
        (_dias_txt(datos.get("entregas_mediana_curso")), "mediana de entregas del curso", TEAL),
        (datos.get("alumnos_en_cero", "—"), "no entregaron nada", RED),
        (datos.get("con_desaprobadas", "—"), "con alguna desaprobada", AMBER),
        (datos.get("esperando_correccion", "—"), "esperando corrección", NAVY),
    ]))
    E.append(Spacer(1, 12))

    if meta.get("degradado"):
        E.append(Paragraph("⚠️ Leé esto antes de los números:", alerta))
        for s in meta.get("sin_dato", [])[:6]:
            E.append(Paragraph(f"· {s}", small))
        E.append(Spacer(1, 8))

    E.append(Paragraph("Cómo leer esto", h2))
    E.append(Paragraph(
        f"<b>No hay porcentaje de avance, y es a propósito.</b> Un porcentaje necesita saber "
        "cuántas actividades ya deberían estar entregadas, y ese dato no existe en este campus: "
        "la mayoría de las actividades no tiene fecha de entrega. Así que se compara contra lo "
        "que el curso realmente hizo: cada alumno contra la <b>mediana de su comisión</b>, y "
        f"cada comisión contra la del curso ({_dias_txt(datos.get('entregas_mediana_curso'))} "
        "entrega(s)). «Entregó 0 donde la mediana de su comisión es 4» es un hecho; «va al 35% "
        "del plan» sería un número inventado.", body))
    E.append(Spacer(1, 4))
    E.append(Paragraph(
        "<b>Tres advertencias.</b> (1) <b>No se puede saber cuándo se matriculó un alumno</b> — "
        "la API no lo expone, está verificado. El que entró tarde entregó menos y NO es peor "
        "alumno. (2) Las notas se leen por el <b>texto</b> de la escala y nunca por el número: "
        "la escala de TPs de este campus va invertida. (3) La lectura de cada comisión dice "
        "<b>qué mirar</b>, no de quién es la culpa.", small))

    focos = focos_de_avance(datos)
    if focos:
        E.append(Paragraph("Focos de hoy", h2))
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
        t = Table(rows, colWidths=[0.9 * cm, 16.1 * cm])
        est = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
               ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, LT])]
        for i in range(len(rows)):
            est.append(("BACKGROUND", (0, i), (0, i), colores[i % len(colores)]))
        t.setStyle(TableStyle(est))
        E.append(t)

    # ---- Por comisión ----
    E.append(PageBreak())
    E.append(Paragraph("Por comisión", h2))
    E.append(Paragraph(
        f"{datos.get('tareas_miradas')} de {datos.get('tareas_pedidas')} actividades relevadas. "
        "Ordenadas por mediana de entregas, la más baja primero. «No abren la materia» es el "
        "cruce que explica el atraso: una comisión atrasada con los alumnos entrando es un "
        "problema distinto de una donde se fueron.", small))
    E.append(Spacer(1, 5))
    rows = [["Com.", "Alum.", "Entregas\nmediana", "Entregas\nmáx", "No entre-\ngaron nada",
             "Desapro-\nbadas", "No abren\nla materia", "Esperando\ncorrección", "Lectura"]]
    for c in coms:
        rows.append([c["comision"], c["alumnos"], _dias_txt(c["entregas_mediana"]),
                     c["entregas_max"], c["alumnos_en_cero"], c["desaprobadas"],
                     c["no_abren_la_materia"], c["esperando_correccion"],
                     Paragraph(c["lectura"], est_celda)])
    E.append(_tabla(rows, [1.2, 1.0, 1.5, 1.2, 1.5, 1.4, 1.5, 1.5, 6.2],
                    alinear_der=[1, 2, 3, 4, 5, 6, 7]))

    # ---- Los que menos entregaron, por comisión ----
    E.append(PageBreak())
    E.append(Paragraph("Alumnos que menos entregaron, por comisión", h2))
    E.append(Paragraph(
        "De cada comisión salen los que están <b>por debajo de la mediana de su propia "
        "comisión</b> más todos los que no entregaron nada. Los demás están en la mediana o "
        "arriba y no se listan — el total va en cada encabezado. «Sin abrir» son los días sin "
        "abrir la materia, no sin entrar al campus.", small))
    E.append(Spacer(1, 6))

    por_com = {}
    for f in flojos:
        por_com.setdefault(f["comision"], []).append(f)
    for c in coms:
        de_com = por_com.get(c["comision"], [])
        med = c["entregas_mediana"] or 0
        lista = [f for f in de_com if f["entregas"] == 0 or f["entregas"] < med]
        E.append(Paragraph(
            f"{c['comision']} — {len(lista)} de {c['alumnos']} alumnos "
            f"(mediana de la comisión: {_dias_txt(c['entregas_mediana'])})", h3))
        if not lista:
            E.append(Paragraph("Nadie por debajo de la mediana de su comisión.", small))
            continue
        cab = ["Alumno", "Entre-\ngadas", "Apro-\nbadas", "Desapro-\nbadas", "Sin abrir\nla materia",
               "Espera\ncorrec. (d)"]
        anchos = [7.3, 1.6, 1.6, 1.9, 2.3, 2.3]
        if emails:
            cab.insert(1, "Email")
            anchos = [4.6, 4.4, 1.4, 1.4, 1.6, 1.9, 1.7]
        rr = [cab]
        for f in sorted(lista, key=lambda x: (x["entregas"],
                                              -(x["dias_sin_abrir_la_materia"] or 0))):
            aula = ("Nunca" if f.get("estado_aula") == "nunca_abrio"
                    else ("—" if f.get("estado_aula") == "sin_dato"
                          else f"{f.get('dias_sin_abrir_la_materia')} d"))
            fila = [Paragraph(f.get("nombre") or "", est_celda), f["entregas"], f["aprobadas"],
                    f["desaprobadas"], aula, _dias_txt(f.get("espera_correccion_dias"))]
            if emails:
                fila.insert(1, Paragraph(f.get("email") or "—", est_celda))
            rr.append(fila)
        E.append(_tabla(rr, anchos,
                        alinear_der=list(range(len(cab) - 5, len(cab)))))
        E.append(Spacer(1, 6))

    E.append(Spacer(1, 8))
    if emails:
        E.append(Paragraph(
            "⚠️ Este documento incluye datos de contacto de alumnos (mails personales). No lo "
            "subas a repositorios ni lo compartas fuera del equipo docente.", alerta))
        E.append(Spacer(1, 4))
    E.append(Paragraph(
        f"UTN · {titulo} · datos leídos EN VIVO de la API REST del campus"
        + (f" el {fecha}" if fecha else "")
        + f" · relevamiento en {meta.get('segundos')} s · sin porcentajes de avance: no hay "
        "fechas de entrega contra las que medirlos.", small))

    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=2.0 * cm, rightMargin=2.0 * cm,
                            title=f"Avance de los alumnos — {titulo}",
                            author="tup-campus-navigator")
    doc.build(E)
    return {"archivo": path, "paginas": getattr(doc, "page", None),
            "incluye_emails": bool(emails), "degradado": bool(meta.get("degradado"))}
