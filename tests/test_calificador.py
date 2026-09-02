"""Tests del lector del LIBRO DE CALIFICACIONES (`moodle/calificador.py`) y de quién es
el TUTOR de una comisión (`moodle/panorama.elegir_tutor`).

Por qué existe este archivo: los dos bugs que motivaron el módulo devolvían un dato
plausible y equivocado, que es la única clase de error que este proyecto persigue de
verdad.

  1. Toda la skill leía `mod_assign` para saber qué hizo un alumno. En Matemática eso
     está en CERO ABSOLUTO (549 participantes, 0 enviados en las 15 actividades) porque
     la cursada pasa por videos H5P, lecciones y autoevaluaciones. El padrón entero salía
     en blanco y eso se lee como "esta comisión no arrancó".
  2. El informe nombraba como tutor al primer docente del padrón, que es el PROFESOR del
     curso. En 13 de las 15 comisiones de Matemática ponía a Jovanovich, Klimovsky o
     Wallace — ninguno de los tres lleva una comisión. `test_tutor_de_matematica` es el
     caso: los datos son los del campus del 2026-09-02 y el reparto esperado es el
     oficial de la cátedra.

Estructuras REALES, traídas del campus el 2026-09-02. No son inventadas a propósito: con
ejemplos limpios los dos bugs son invisibles.

Correr:  .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from moodle import calificador, titulos  # noqa: E402
from moodle.panorama import elegir_tutor  # noqa: E402


def _mod(cmid, name, modname):
    return {"id": cmid, "name": name, "modname": modname}


# La estructura de Matemática (course 77), recortada a lo que importa: los seis
# encabezados numerados, sus sub-bloques, y las tres secciones que NO son de unidad y
# están puestas justo entre medio — que es donde estaba la trampa.
SECCIONES_MATEMATICA = [
    {"name": "General", "modules": []},
    {"name": "Introducción General",
     "modules": [_mod(20808, "Video_Inicial_Interactivo", "hvp")]},
    {"name": "1 - Algebra de Boole", "modules": []},
    {"name": "Videos", "modules": [_mod(18423, "Video_Semana1_AB_Interactivo", "hvp"),
                                   _mod(18424, "Video_Semana2_AB_Interactivo", "hvp")]},
    {"name": "Lecciones", "modules": [_mod(18426, "Algebra de Boole Lección Semana 1", "lesson")]},
    {"name": "Trabajo Práctico",
     "modules": [_mod(18437, "ENTREGA U1S1: Ejercicio 8 de la Práctica 1", "assign")]},
    {"name": "Autoevaluaciones",
     "modules": [_mod(18439, "Autoevaluación Álgebra de Boole Semana 1", "quiz")]},
    {"name": "2- Sistema binario", "modules": []},
    {"name": "Videos", "modules": [_mod(18454, "Video 1 Semana 1 SN", "hvp"),
                                   _mod(18460, "Video 4 Semana 2 SN", "hvp")]},
    {"name": "Trabajo Práctico",
     "modules": [_mod(18469, "ENTREGA U2S1: Ejercicio 8 de la Práctica 1", "assign")]},
    {"name": "Coloquios y Trabajo Integrador I",
     "modules": [_mod(18519, "Entrega trabajo 1", "assign")]},
    {"name": "6 - Arboles y Grafos", "modules": []},
    {"name": "Videos", "modules": [_mod(18616, "Video 1 Semana 1 Grafos", "hvp")]},
    {"name": "Actividades Lúdicas", "modules": [_mod(18626, "Juego semana 1", "hvp")]},
    {"name": "COLOQUIOS",
     "modules": [_mod(18670, "Cuestionario de Algebra de Boole", "quiz"),
                 _mod(18675, "Cuestionario de Arboles y Grafos", "quiz")]},
    {"name": "Chat con IA  y Foro de consultas",
     "modules": [_mod(18684, "Foro de consultas academicas", "forum")]},
]


class TestCatalogo(unittest.TestCase):
    def setUp(self):
        self.cat = calificador.catalogo_de_actividades(SECCIONES_MATEMATICA)
        self.por_titulo = {it["titulo"]: it for it in self.cat["items"]}

    def test_los_subbloques_heredan_la_unidad_del_encabezado(self):
        """"Videos"/"Lecciones"/"Trabajo Práctico" no dicen de qué unidad son: la
        unidad la abre la sección numerada de más arriba."""
        self.assertEqual(self.por_titulo["Video_Semana1_AB_Interactivo"]["unidad"], 1)
        self.assertEqual(self.por_titulo["Algebra de Boole Lección Semana 1"]["unidad"], 1)
        self.assertEqual(self.por_titulo["Video 1 Semana 1 SN"]["unidad"], 2)
        self.assertEqual(self.por_titulo["Video 1 Semana 1 Grafos"]["unidad"], 6)

    def test_una_seccion_con_nombre_propio_corta_la_herencia(self):
        """EL caso. Los 7 cuestionarios de COLOQUIOS están DESPUÉS de la unidad 6 y no
        son de la unidad 6: heredar por posición les habría puesto un 6 que se lee
        perfecto y es falso. Lo mismo con los integradores."""
        self.assertIsNone(self.por_titulo["Cuestionario de Algebra de Boole"]["unidad"])
        self.assertIsNone(self.por_titulo["Cuestionario de Arboles y Grafos"]["unidad"])
        self.assertIsNone(self.por_titulo["Entrega trabajo 1"]["unidad"])
        self.assertIsNone(self.por_titulo["Video_Inicial_Interactivo"]["unidad"])

    def test_lo_que_queda_sin_unidad_se_declara(self):
        """No se puede quedar callado: quien lee tiene que saber que hay actividades
        fuera del corte por unidad."""
        self.assertTrue(any("no quedaron bajo ninguna unidad" in a
                            for a in self.cat["avisos"]))

    def test_las_entregas_verifican_la_lectura_por_seccion(self):
        """Las tareas `ENTREGA U{n}S{m}` sí declaran su unidad en el título, así que
        sirven de control: si la sección las pone en otra unidad, algo se leyó mal."""
        for it in self.cat["items"]:
            leido = titulos.leer(it["titulo"])
            if leido.unidad is not None:
                self.assertEqual(it["unidad"], leido.unidad, it["titulo"])

    def test_el_titulo_le_gana_a_la_seccion_y_lo_avisa(self):
        """Misma precedencia que `titulos.py`: el código estructurado gana. Y el
        desacuerdo NO se resuelve en silencio."""
        secciones = [
            {"name": "3- Lógica", "modules": []},
            {"name": "Trabajo Práctico",
             "modules": [_mod(1, "ENTREGA U5S1: puesta en la sección equivocada", "assign")]},
        ]
        cat = calificador.catalogo_de_actividades(secciones)
        self.assertEqual(cat["items"][0]["unidad"], 5)
        self.assertTrue(any("declara la unidad 5" in a for a in cat["avisos"]))

    def test_solo_entran_las_actividades_calificables(self):
        """Un foro no es una actividad del alumno con nota."""
        self.assertNotIn("Foro de consultas academicas", self.por_titulo)

    def test_cada_modulo_es_su_tipo(self):
        self.assertEqual(self.por_titulo["Video 1 Semana 1 SN"]["tipo"], "video")
        self.assertEqual(self.por_titulo["Algebra de Boole Lección Semana 1"]["tipo"],
                         "leccion")
        self.assertEqual(self.por_titulo["Autoevaluación Álgebra de Boole Semana 1"]["tipo"],
                         "autoevaluacion")
        self.assertEqual(self.por_titulo["Entrega trabajo 1"]["tipo"], "entrega")

    def test_el_orden_es_el_de_la_cursada(self):
        """Por unidad, y dentro de la unidad por tipo y por SEMANA. Ordenar por título
        pone "Video 4 Semana 1" después de "Video 1 Semana 2"."""
        unidades = [it["unidad"] for it in self.cat["items"]]
        con_unidad = [u for u in unidades if u is not None]
        self.assertEqual(con_unidad, sorted(con_unidad))
        # Lo que no declara unidad va al final y no se mezcla.
        self.assertIsNone(unidades[-1])


class TestNotas(unittest.TestCase):
    def setUp(self):
        self.cat = calificador.catalogo_de_actividades(SECCIONES_MATEMATICA)

    def test_moodle_mete_html_adentro_de_la_nota(self):
        """`gradeformatted` llega con el ícono de aprobado pegado al número. Sin
        limpiarlo, las celdas CON nota se ven peor que las vacías."""
        crudo = ('<i class="afaicon fa fa-check text-success inline fa-fw" '
                 'title="Aprobado" role="img" aria-label="Aprobado"></i>10,00')
        self.assertEqual(calificador.limpiar_nota(crudo), "10,00")
        self.assertEqual(calificador.limpiar_nota("-"), "-")
        self.assertEqual(calificador.limpiar_nota(None), "")

    def test_sin_nota_no_es_un_cero(self):
        """`graderaw: None` es "no la hizo". Contarlo como 0 hunde el promedio de quien
        viene al día y hace desaparecer la diferencia entre no hacerla y hacerla mal."""
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"cmid": 18423, "graderaw": None, "grademax": 10, "gradeformatted": "-"},
            {"cmid": 18424, "graderaw": 8.5, "grademax": 10, "gradeformatted": "8,50"},
        ]}]
        f = calificador.filas_de_alumnos(ug, self.cat)[0]
        self.assertEqual(f["por_tipo"]["video"]["hechas"], 1)
        self.assertNotIn(18423, f["notas"])
        self.assertEqual(f["notas"][18424]["nota"], 8.5)
        self.assertEqual(f["por_tipo"]["video"]["promedio_pct"], 85.0)

    def test_el_total_por_tipo_es_el_del_curso_no_el_del_alumno(self):
        """El denominador tiene que ser cuántas hay, no cuántas le llegaron en la
        respuesta: si no, todos entregan el 100%."""
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"cmid": 18423, "graderaw": 10, "grademax": 10}]}]
        f = calificador.filas_de_alumnos(ug, self.cat)[0]
        self.assertEqual(f["por_tipo"]["video"]["hechas"], 1)
        self.assertEqual(f["por_tipo"]["video"]["total"], 1)  # sólo llegó una

    def test_el_item_de_total_del_curso_no_es_una_actividad(self):
        """`itemtype: course` viene en la misma respuesta y no tiene `cmid`. Contarlo
        como actividad le suma a todo el mundo una que no existe."""
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"itemtype": "course", "cmid": None, "graderaw": 74.3, "grademax": 100},
            {"cmid": 18423, "graderaw": 10, "grademax": 10}]}]
        f = calificador.filas_de_alumnos(ug, self.cat)[0]
        self.assertEqual(f["actividades_con_nota"], 1)

    def test_sin_el_reloj_del_aula_se_dice_sin_dato_y_no_nunca_abrio(self):
        """El campo puede no venir por permisos. "Nunca abrió la materia" es un hecho
        del campus y "no pude leerlo" es otra cosa: confundirlos manda a llamar a
        alguien por algo que no pasó."""
        ug = [{"userid": 7, "userfullname": "PEPE", "gradeitems": []}]
        sin = calificador.filas_de_alumnos(ug, self.cat, {})[0]
        self.assertEqual(sin["estado_aula"], "sin_dato")
        nunca = calificador.filas_de_alumnos(ug, self.cat, {7: {"lastcourseaccess": 0}})[0]
        self.assertEqual(nunca["estado_aula"], "nunca_abrio")
        abrio = calificador.filas_de_alumnos(
            ug, self.cat, {7: {"lastcourseaccess": 1788300000, "lastaccess": 1788300000}})[0]
        self.assertEqual(abrio["estado_aula"], "abrio")
        self.assertIsNotNone(abrio["dias_sin_abrir_la_materia"])


# El padrón docente REAL de Matemática, del 2026-09-02. Los `editingteacher` que se
# repiten son la cátedra; los `teacher` que aparecen una vez son los tutores.
_D = lambda uid, nombre, rol: {"userid": uid, "nombre": nombre, "rol": rol}  # noqa: E731
MATEMATICA_DOCENTES = {
    7720: [_D(1588, "Ethel Carina Jovanovich", "editingteacher"),
           _D(9001, "Gisella Villalba", "teacher")],
    8320: [_D(1588, "Ethel Carina Jovanovich", "editingteacher"),
           _D(9002, "Valeria Celerier", "teacher")],
    7729: [_D(1588, "Ethel Carina Jovanovich", "editingteacher"),
           _D(9003, "Maria Teresa Brizzi", "teacher")],
    7732: [_D(1588, "Ethel Carina Jovanovich", "editingteacher"),
           _D(9004, "Daniel Luis Mosqueda", "teacher")],
    7736: [_D(9, "Cristian Mut", "editingteacher"),
           _D(1588, "Ethel Carina Jovanovich", "editingteacher")],
    7741: [_D(1589, "Ernesto Klimovsky", "editingteacher"),
           _D(9006, "Valentina Gonella", "teacher")],
    7744: [_D(1589, "Ernesto Klimovsky", "editingteacher"),
           _D(9007, "Monica Leguiza", "teacher")],
    7748: [_D(14, "Sergio Maldonado", "editingteacher"),
           _D(1589, "Ernesto Klimovsky", "editingteacher")],
    7753: [_D(1589, "Ernesto Klimovsky", "editingteacher"),
           _D(9009, "Federico Esteban Rodríguez", "teacher")],
    7757: [_D(1590, "Martina Wallace", "editingteacher"),
           _D(9010, "Fernanda Espósito", "teacher")],
    7761: [_D(1590, "Martina Wallace", "editingteacher"),
           _D(9011, "Ramiro Escobar", "teacher")],
    7765: [_D(1590, "Martina Wallace", "editingteacher"),
           _D(9012, "Miguel Barrera Oltra", "teacher")],
    # com13 es el caso difícil: TRES docentes, y Brizzi (que es la tutora de com3)
    # también está acá. Gana Castro, que es la única que aparece una sola vez.
    7768: [_D(1590, "Martina Wallace", "editingteacher"),
           _D(9003, "Maria Teresa Brizzi", "teacher"),
           _D(9013, "Ana María Castro", "teacher")],
    7773: [_D(1758, "Demian Bogado", "editingteacher")],
    7777: [_D(1768, "Sebastian Marinier", "editingteacher")],
}
REPARTO_OFICIAL = {
    7720: "Gisella Villalba", 8320: "Valeria Celerier", 7729: "Maria Teresa Brizzi",
    7732: "Daniel Luis Mosqueda", 7736: "Cristian Mut", 7741: "Valentina Gonella",
    7744: "Monica Leguiza", 7748: "Sergio Maldonado",
    7753: "Federico Esteban Rodríguez", 7757: "Fernanda Espósito",
    7761: "Ramiro Escobar", 7765: "Miguel Barrera Oltra", 7768: "Ana María Castro",
    7773: "Demian Bogado", 7777: "Sebastian Marinier",
}


class TestElegirTutor(unittest.TestCase):
    def setUp(self):
        self.comisiones = [{"comision": f"com{i}", "group_id": gid}
                           for i, gid in enumerate(MATEMATICA_DOCENTES, start=1)]
        self.padrones = {gid: {"docentes": ds}
                         for gid, ds in MATEMATICA_DOCENTES.items()}

    def test_tutor_de_matematica(self):
        """EL caso. Contra el reparto oficial de la cátedra: 15 de 15. Antes daba 4,
        porque tomaba el primero del padrón y el web service ordena por userid — o sea,
        devolvía sistemáticamente a la cuenta más vieja, que es la de cátedra."""
        elegidos = elegir_tutor(self.padrones, self.comisiones)
        for gid, esperado in REPARTO_OFICIAL.items():
            self.assertEqual(elegidos[gid]["tutor"]["nombre"], esperado,
                             f"group_id {gid}")

    def test_el_profesor_que_cubre_varias_comisiones_no_es_el_tutor(self):
        """Jovanovich está en 5 comisiones, Klimovsky en 4, Wallace en 4. Ninguno
        puede ser el tutor de ninguna: nombrarlos es adjudicarle a un docente el
        trabajo (o el atraso) de otro."""
        elegidos = elegir_tutor(self.padrones, self.comisiones)
        nombrados = {e["tutor"]["nombre"] for e in elegidos.values() if e["tutor"]}
        for catedra in ("Ethel Carina Jovanovich", "Ernesto Klimovsky", "Martina Wallace"):
            self.assertNotIn(catedra, nombrados)

    def test_una_comision_con_un_solo_docente_no_cambia(self):
        """Programación tiene un docente por comisión y ahí no hay nada que desempatar:
        la regla nueva no puede mover una sola fila."""
        comisiones = [{"comision": "com1", "group_id": 1},
                      {"comision": "com2", "group_id": 2}]
        padrones = {1: {"docentes": [_D(50, "Martina Belen Zabala", "editingteacher")]},
                    2: {"docentes": [_D(51, "Maxiimiliano Sar Fernandez", "teacher")]}}
        elegidos = elegir_tutor(padrones, comisiones)
        self.assertEqual(elegidos[1]["tutor"]["nombre"], "Martina Belen Zabala")
        self.assertEqual(elegidos[2]["tutor"]["nombre"], "Maxiimiliano Sar Fernandez")
        self.assertEqual(elegidos[1]["avisos"], [])

    def test_una_comision_sin_docente_es_un_hallazgo_no_un_blanco(self):
        elegidos = elegir_tutor({1: {"docentes": []}}, [{"comision": "com1", "group_id": 1}])
        self.assertIsNone(elegidos[1]["tutor"])
        self.assertTrue(any("SIN DOCENTE" in a for a in elegidos[1]["avisos"]))

    def test_cuando_el_alcance_empata_se_dice_que_el_criterio_es_debil(self):
        """Sin diferencia de alcance decide el rol, que es más débil. Callarlo sería
        entregar un nombre con la misma cara de dato bueno que los otros catorce."""
        comisiones = [{"comision": "com1", "group_id": 1}]
        padrones = {1: {"docentes": [_D(10, "Uno", "editingteacher"),
                                     _D(20, "Dos", "teacher")]}}
        elegidos = elegir_tutor(padrones, comisiones)
        self.assertEqual(elegidos[1]["tutor"]["nombre"], "Dos")
        self.assertTrue(any("más débil" in a for a in elegidos[1]["avisos"]))


class TestSemanaSuelta(unittest.TestCase):
    """El calificador de Matemática declara la SEMANA y nunca la unidad."""

    def test_la_semana_sola_se_lee(self):
        self.assertEqual(titulos.leer("Video 2 Semana 1 SN").semana, 1)
        self.assertEqual(titulos.leer("Sistema Binario Lección semana 2").semana, 2)

    def test_la_semana_sola_no_inventa_una_unidad(self):
        """Es el eje débil: no alcanza para decir de qué unidad es la actividad."""
        act = titulos.leer("Video 2 Semana 1 SN")
        self.assertIsNone(act.unidad)
        self.assertIsNone(act.etiqueta)
        self.assertFalse(act.es_cadencia)

    def test_no_le_toca_nada_a_programacion(self):
        """Ningún título de Programación dice "semana": la etiqueta de columna del panel
        no se puede mover por este cambio."""
        self.assertEqual(titulos.etiqueta("Actividad de cierre unidad 4 - Git 🎯🏁"), "U4")
        self.assertEqual(titulos.etiqueta("ENTREGA U5S3: Ejercicio semana 3"), "U5S3")


if __name__ == "__main__":
    unittest.main()
