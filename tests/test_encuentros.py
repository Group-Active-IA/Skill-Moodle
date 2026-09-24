"""Tests del MATERIAL de un encuentro (`moodle/encuentros.py`).

Por qué existe este archivo: la falla que persigue este módulo no tira excepción. Si
`elegir_apunte` se equivoca de link, la tool baja algo, lo devuelve como material y el
agente le contesta a un alumno con total confianza sobre el tema equivocado. Nadie se
entera hasta que el alumno lo dice en el foro — delante de las 27 comisiones.

Los tres modos de falla que cubren estos tests, los tres silenciosos:

  1. Elegir el link equivocado. La sección mezcla apunte, videos de YouTube, salas de
     Teams/Zoom y links al propio campus. Verificado en vivo (curso 74, 2026-09-23): el
     label de la modalidad nueva trae los tres tipos juntos.
  2. Filtrar por `visible`. El label del encuentro y el foro están OCULTOS a los
     estudiantes hasta la hora del encuentro. Saltearlos por eso deja al agente sin
     material justo en el momento de prepararlo.
  3. Devolver el HTML con el `<script>` adentro. Un strip de tags a secas deja el estado
     embebido del framework, y el agente lo lee como si fuera contenido del apunte.

Las estructuras son las REALES que devolvió `core_course_get_contents` para el curso 74
el 2026-09-23. No son inventadas a propósito: con una sección limpia de un solo link, los
tres bugs son invisibles.

Correr:  .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from moodle import encuentros  # noqa: E402

BASE = "https://tup.sied.utn.edu.ar"

# Sección 65 del curso 74, tal como vino del campus el 2026-09-23. Los dos labels
# conviven: el 21443 es la modalidad nueva (oculta) y el 17914 el listado viejo de
# encuentros con sus grabaciones y salas.
SECCION_REAL = {
    "section": 65,
    "name": "Encuentros síncronos",
    "visible": 1,
    "summary": "",
    "modules": [
        {
            "id": 21443, "modname": "label", "visible": 0,
            "name": "Nueva modalidad - Encuentros Sincrónicos",
            "description": (
                '<a href="https://www.youtube.com/playlist?list=PLS2LiS-uOZOs">playlist</a>'
                '<a href="https://datos-complejos.vercel.app/">apunte</a>'
                '<a href="https://tup.sied.utn.edu.ar/mod/forum/view.php?id=21444">foro</a>'
            ),
        },
        {
            "id": 21444, "modname": "forum", "visible": 0,
            "name": "💬 Consultas y acompañamiento — Encuentro Sincrónico",
            "description": "",
            "url": "https://tup.sied.utn.edu.ar/mod/forum/view.php?id=21444",
        },
        {
            "id": 17914, "modname": "label", "visible": 1,
            "name": "Encuentros Sincronicos",
            "description": (
                '<a href="https://youtu.be/CpBXJIhxKzs">grabacion</a>'
                '<a href="https://youtu.be/XnhBNpSQ83c">grabacion</a>'
                '<a href="https://teams.microsoft.com/meet/274722614555981?p=oYFfD1">meet</a>'
                '<a href="https://teams.live.com/meet/9358305817284?p=NcUHIK">meet</a>'
                '<a href="https://utn.zoom.us/j/82837328028">zoom</a>'
            ),
        },
    ],
}


class TestElegirApunte(unittest.TestCase):
    """El link correcto entre videos, salas de reunión y links al propio campus."""

    def test_elige_el_apunte_y_no_el_video_ni_la_sala(self):
        links = encuentros.links_de_seccion(SECCION_REAL)
        r = encuentros.elegir_apunte(links, BASE)
        self.assertIsNotNone(r["elegido"], r["motivo"])
        self.assertEqual(r["elegido"]["url"], "https://datos-complejos.vercel.app/")
        self.assertEqual(r["elegido"]["cmid"], 21443)
        self.assertEqual(len(r["candidatos"]), 1, "un solo candidato en la sección real")

    def test_descarta_cada_tipo_con_su_motivo(self):
        links = encuentros.links_de_seccion(SECCION_REAL)
        motivos = {d["url"]: d["motivo"] for d in encuentros.elegir_apunte(links, BASE)["descartados"]}
        self.assertEqual(motivos["https://www.youtube.com/playlist?list=PLS2LiS-uOZOs"], "es un video")
        self.assertEqual(motivos["https://youtu.be/CpBXJIhxKzs"], "es un video")
        self.assertEqual(motivos["https://utn.zoom.us/j/82837328028"], "es una sala de reunión")
        self.assertEqual(motivos["https://teams.live.com/meet/9358305817284?p=NcUHIK"],
                         "es una sala de reunión")
        self.assertEqual(motivos["https://tup.sied.utn.edu.ar/mod/forum/view.php?id=21444"],
                         "es del propio campus")

    def test_utn_zoom_us_es_sala_aunque_sea_subdominio(self):
        """`utn.zoom.us` no está en la lista literal: se compara por sufijo. Sin eso
        entraba como candidato y competía con el apunte."""
        r = encuentros.elegir_apunte([{"url": "https://utn.zoom.us/j/1", "cmid": 1}], BASE)
        self.assertIsNone(r["elegido"])

    def test_sin_candidatos_dice_no_pude_en_vez_de_elegir(self):
        solo_videos = [{"url": "https://youtu.be/abc", "cmid": 1}]
        r = encuentros.elegir_apunte(solo_videos, BASE)
        self.assertIsNone(r["elegido"])
        self.assertIn("Ningún link", r["motivo"])

    def test_varios_candidatos_avisa_y_toma_el_mas_nuevo(self):
        """El modo de falla peligroso: dos apuntes y elegir el de la unidad pasada sin
        decirlo. Se toma el cmid más alto Y queda declarado en el motivo."""
        dos = [{"url": "https://listas.vercel.app/", "cmid": 20000},
               {"url": "https://datos-complejos.vercel.app/", "cmid": 21443}]
        r = encuentros.elegir_apunte(dos, BASE)
        self.assertEqual(r["elegido"]["url"], "https://datos-complejos.vercel.app/")
        self.assertEqual(len(r["candidatos"]), 2)
        self.assertIn("2 candidatos", r["motivo"])
        self.assertIn("url", r["motivo"], "tiene que decir cómo forzar otro apunte")

    def test_descarta_lo_que_no_es_http(self):
        r = encuentros.elegir_apunte([{"url": "mailto:tutor@utn.edu.ar", "cmid": 1}], BASE)
        self.assertIsNone(r["elegido"])


class TestLinksDeSeccion(unittest.TestCase):
    def test_incluye_modulos_ocultos(self):
        """El label 21443 y el foro 21444 están en `visible: 0` hasta la hora del
        encuentro. Filtrarlos deja al tutor sin material justo cuando lo prepara."""
        urls = [l["url"] for l in encuentros.links_de_seccion(SECCION_REAL)]
        self.assertIn("https://datos-complejos.vercel.app/", urls)

    def test_le_pone_a_cada_link_el_cmid_de_su_modulo(self):
        por_url = {l["url"]: l["cmid"] for l in encuentros.links_de_seccion(SECCION_REAL)}
        self.assertEqual(por_url["https://datos-complejos.vercel.app/"], 21443)
        self.assertEqual(por_url["https://youtu.be/CpBXJIhxKzs"], 17914)

    def test_toma_los_links_del_summary_de_la_seccion(self):
        sec = {"name": "Encuentros", "summary": '<a href="https://x.vercel.app/">a</a>',
               "modules": []}
        self.assertEqual(encuentros.links_de_seccion(sec),
                         [{"url": "https://x.vercel.app/", "cmid": None}])


class TestSeccionesDeEncuentros(unittest.TestCase):
    def test_matchea_con_y_sin_acento(self):
        secs = [{"name": "Encuentros síncronos"}, {"name": "Encuentros Sincronicos"},
                {"name": "5- Listas"}]
        self.assertEqual(len(encuentros.secciones_de_encuentros(secs)), 2)

    def test_curso_sin_encuentros_devuelve_vacio(self):
        self.assertEqual(encuentros.secciones_de_encuentros([{"name": "1- Secuenciales"}]), [])


class TestHtmlATexto(unittest.TestCase):
    def test_saca_script_y_style_enteros(self):
        """El bug silencioso: un strip de tags a secas deja el contenido del `<script>`,
        y el agente lo lee como si fuera parte del apunte."""
        html = ("<p>Una tupla es inmutable</p>"
                '<script id="__NEXT_DATA__">{"props":{"basura":"no es contenido"}}</script>'
                "<style>.x{color:red}</style>")
        t = encuentros.html_a_texto(html)
        self.assertIn("Una tupla es inmutable", t)
        self.assertNotIn("basura", t)
        self.assertNotIn("color:red", t)

    def test_conserva_los_saltos_de_bloque(self):
        t = encuentros.html_a_texto("<h2>Sets</h2><p>Valores únicos</p><p>Sin orden</p>")
        self.assertEqual([l for l in t.split("\n") if l], ["Sets", "Valores únicos", "Sin orden"])

    def test_desescapa_entidades(self):
        self.assertIn("{1,2} & {2,3}", encuentros.html_a_texto("<p>{1,2} &amp; {2,3}</p>"))


class TestTituloDe(unittest.TestCase):
    def test_saca_el_title(self):
        html = "<html><head><title>Unidad 7 · Datos Complejas</title></head></html>"
        self.assertEqual(encuentros.titulo_de(html), "Unidad 7 · Datos Complejas")

    def test_sin_title_devuelve_vacio_no_revienta(self):
        self.assertEqual(encuentros.titulo_de("<html></html>"), "")


if __name__ == "__main__":
    unittest.main()
