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

from moodle import calificador, grupos, titulos  # noqa: E402
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

    def test_quien_decide_que_se_califica_es_el_CALIFICADOR_no_el_arbol(self):
        """Un `forum` sólo tiene nota si la cátedra lo configuró calificable, y eso el
        árbol del curso NO lo dice. El de Matemática es de consultas y no se califica: si
        el catálogo decidiera solo, agregaría una columna donde nadie tiene nota nunca.
        El catálogo lo lista con su metadata y `items_de_calificador` lo deja afuera."""
        self.assertIn("Foro de consultas academicas", self.por_titulo)
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"id": 900, "cmid": 18423, "itemnumber": 0, "itemname": "Video_Semana1_AB_Interactivo",
             "graderaw": 10, "grademax": 10}]}]
        cols = calificador.items_de_calificador(ug, self.cat)
        self.assertEqual([i["titulo"] for i in cols["items"]],
                         ["Video_Semana1_AB_Interactivo"])

    def test_cada_modulo_es_su_tipo(self):
        self.assertEqual(self.por_titulo["Video 1 Semana 1 SN"]["tipo"], "video")
        self.assertEqual(self.por_titulo["Algebra de Boole Lección Semana 1"]["tipo"],
                         "leccion")
        self.assertEqual(self.por_titulo["Autoevaluación Álgebra de Boole Semana 1"]["tipo"],
                         "cuestionario")
        self.assertEqual(self.por_titulo["Entrega trabajo 1"]["tipo"], "entrega")

    def test_matematica_numera_UNIDADES(self):
        """Ningún título de Matemática declara semana con el prefijo `S«n»-`, así que el
        número de la sección es la unidad. Es el lado del criterio que ya funcionaba y que
        el soporte de Probabilidad y Estadística no puede romper."""
        self.assertEqual(self.cat["eje"], "unidad")
        self.assertEqual(calificador.eje_de_secciones(SECCIONES_MATEMATICA)[1], 0)

    def test_el_orden_es_el_de_la_cursada(self):
        """Por unidad, y dentro de la unidad por tipo y por SEMANA. Ordenar por título
        pone "Video 4 Semana 1" después de "Video 1 Semana 2"."""
        unidades = [it["unidad"] for it in self.cat["items"]]
        con_unidad = [u for u in unidades if u is not None]
        self.assertEqual(con_unidad, sorted(con_unidad))
        # Lo que no declara unidad va al final y no se mezcla.
        self.assertIsNone(unidades[-1])


def _cols(cat, ug):
    """Las columnas reales, como las arma el informe."""
    return calificador.items_de_calificador(ug, cat)


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
            {"id": 1, "cmid": 18423, "itemnumber": 0, "graderaw": None, "grademax": 10,
             "gradeformatted": "-"},
            {"id": 2, "cmid": 18424, "itemnumber": 0, "graderaw": 8.5, "grademax": 10,
             "gradeformatted": "8,50"},
        ]}]
        f = calificador.filas_de_alumnos(ug, _cols(self.cat, ug))[0]
        self.assertEqual(f["por_tipo"]["video"]["hechas"], 1)
        self.assertNotIn(1, f["notas"])
        self.assertEqual(f["notas"][2]["nota"], 8.5)
        self.assertEqual(f["por_tipo"]["video"]["promedio_pct"], 85.0)

    def test_dos_columnas_de_nota_con_el_MISMO_cmid_no_se_pisan(self):
        """EL bug. Un foro calificado devuelve DOS grade items con el mismo `cmid`,
        distinguidos por `itemnumber`. Este módulo indexaba por `cmid` y perdía la mitad
        de las notas del foro sin decir nada. La clave es el `id` del grade item.

        No se disparó en Matemática porque ahí no hay foros calificados; en Probabilidad y
        Estadística son cuatro, con 22 notas por comisión.
        """
        secs = [{"name": "1 - Unidad", "modules": []},
                {"name": "Actividades", "modules": [_mod(500, "Foro calificado", "forum")]}]
        cat = calificador.catalogo_de_actividades(secs)
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"id": 10, "cmid": 500, "itemnumber": 0, "itemname": "Foro calificado",
             "graderaw": 2, "grademax": 2},
            {"id": 11, "cmid": 500, "itemnumber": 1, "itemname": "Foro calificado",
             "graderaw": 1, "grademax": 2},
        ]}]
        cols = _cols(cat, ug)
        self.assertEqual(len(cols["items"]), 2)
        # y se distinguen en la leyenda, que si no son dos columnas con el mismo nombre
        self.assertNotEqual(cols["items"][0]["titulo"], cols["items"][1]["titulo"])
        f = calificador.filas_de_alumnos(ug, cols)[0]
        self.assertEqual(len(f["notas"]), 2)
        self.assertEqual({n["nota"] for n in f["notas"].values()}, {1.0, 2.0})

    def test_el_total_por_tipo_es_el_del_curso_no_el_del_alumno(self):
        """El denominador tiene que ser cuántas hay, no cuántas hizo: si no, todos
        entregan el 100%."""
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"id": 1, "cmid": 18423, "itemnumber": 0, "graderaw": 10, "grademax": 10},
            {"id": 2, "cmid": 18424, "itemnumber": 0, "graderaw": None, "grademax": 10}]}]
        f = calificador.filas_de_alumnos(ug, _cols(self.cat, ug))[0]
        self.assertEqual(f["por_tipo"]["video"]["hechas"], 1)
        self.assertEqual(f["por_tipo"]["video"]["total"], 2)

    def test_el_item_de_total_del_curso_no_es_una_actividad(self):
        """`itemtype: course` viene en la misma respuesta y no tiene `cmid`. Contarlo
        como actividad le suma a todo el mundo una que no existe."""
        ug = [{"userid": 1, "userfullname": "PEPE", "gradeitems": [
            {"id": 99, "itemtype": "course", "cmid": None, "graderaw": 74.3, "grademax": 100},
            {"id": 1, "cmid": 18423, "itemnumber": 0, "graderaw": 10, "grademax": 10}]}]
        cols = _cols(self.cat, ug)
        self.assertEqual(len(cols["items"]), 1)
        f = calificador.filas_de_alumnos(ug, cols)[0]
        self.assertEqual(f["actividades_con_nota"], 1)

    def test_sin_el_reloj_del_aula_se_dice_sin_dato_y_no_nunca_abrio(self):
        """El campo puede no venir por permisos. "Nunca abrió la materia" es un hecho
        del campus y "no pude leerlo" es otra cosa: confundirlos manda a llamar a
        alguien por algo que no pasó."""
        ug = [{"userid": 7, "userfullname": "PEPE", "gradeitems": []}]
        cols = _cols(self.cat, ug)
        sin = calificador.filas_de_alumnos(ug, cols, {})[0]
        self.assertEqual(sin["estado_aula"], "sin_dato")
        nunca = calificador.filas_de_alumnos(ug, cols, {7: {"lastcourseaccess": 0}})[0]
        self.assertEqual(nunca["estado_aula"], "nunca_abrio")
        abrio = calificador.filas_de_alumnos(
            ug, cols, {7: {"lastcourseaccess": 1788300000, "lastaccess": 1788300000}})[0]
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




# La estructura REAL de Probabilidad y Estadística (course 79), recortada. Es la materia
# que rompió tres criterios a la vez: sus comisiones se llaman "Comisión 7", sus secciones
# numeradas son SEMANAS y no unidades, y su cadencia son 36 CUESTIONARIOS —una palabra que
# contiene "tio" y por eso caía entera en la lista de exclusiones.
SECCIONES_PYE = [
    {"name": "👋🏻 Introducción y Bienvenida", "modules": []},
    {"name": "1- Identificar y Clasificar Datos", "modules": [
        _mod(20399, "Espacio para Consultas y Dudas sobre la UNIDAD N° 1📓", "forum")]},
    {"name": "🏋️Ejercitación", "modules": [
        _mod(18873, "S1- Ejercitación Población y Muestra, Variables, Escalas", "quiz")]},
    {"name": "2- Organizar Datos", "modules": []},
    {"name": "📝Ejercitación", "modules": [
        _mod(18882, "S2-Ejercitación: interpretación de tablas de frecuencias", "quiz")]},
    {"name": "3- Analizar y Resumir Datos", "modules": []},
    {"name": "🛠️ Trabajo de Aplicación Integrador", "modules": [
        _mod(18909, "Primera entrega del TAI – Consignas 1 y 2", "assign")]},
    {"name": "5- Aprender a Contar", "modules": [
        _mod(18914, "Espacio para consultas y dudas sobre la Unidad N° 2: Probabilidad", "forum")]},
    {"name": "Ejercitación 🤹", "modules": [
        _mod(18921, "S5-Cuestionario: técnicas de conteo", "quiz")]},
    {"name": "8 - Repaso Unidades N° 1 y 2", "modules": []},
    {"name": "Repaso Unidad N° 1", "modules": [
        _mod(18953, "S8-Cuestionario 1, revisión Unidad 1", "quiz")]},
    {"name": "9- Primer Parcial", "modules": [
        _mod(18963, "Primer Examen Parcial", "quiz")]},
    {"name": "10- Modelar lo Aleatorio", "modules": [
        _mod(18972, "Espacio para Consultas y Dudas sobre la Unidad 3", "forum")]},
    {"name": "Ejercitación 🏋️", "modules": [
        _mod(18978, "S10- Ejercitación de Modelo Binomial", "quiz")]},
    {"name": "Condiciones Finales", "modules": [
        _mod(19071, "CONDICIONES FINALES", "assign"),
        _mod(19073, "Calificación Coloquio", "assign")]},
]


class TestProbabilidadYEstadistica(unittest.TestCase):
    """La tercera materia, y la que obligó a que el EJE de las secciones se derive.

    Matemática numera 6 secciones y son sus 6 unidades. PyE numera 16 y son 16 SEMANAS
    agrupadas en 4 unidades. Leer el número de sección como unidad devuelve "unidad 16" en
    una materia de cuatro — plausible y falso, que es la única clase de error que importa.
    """

    def setUp(self):
        self.cat = calificador.catalogo_de_actividades(SECCIONES_PYE)
        self.por_titulo = {it["titulo"]: it for it in self.cat["items"]}

    def test_el_eje_se_deriva_del_dato_y_no_se_configura(self):
        eje, coincidencias = calificador.eje_de_secciones(SECCIONES_PYE)
        self.assertEqual(eje, "semana")
        self.assertGreaterEqual(coincidencias, 3)
        self.assertEqual(self.cat["eje"], "semana")

    def test_la_seccion_numerada_es_la_SEMANA(self):
        self.assertEqual(self.por_titulo["S5-Cuestionario: técnicas de conteo"]["semana"], 5)
        self.assertEqual(
            self.por_titulo["S10- Ejercitación de Modelo Binomial"]["semana"], 10)
        # y NO hay ninguna "unidad 10": las unidades de esta materia son 4
        self.assertLessEqual(max(self.cat["unidades"] or [0]), 4)

    def test_la_unidad_la_abre_el_foro_de_consultas(self):
        """Es lo único del aula que nombra la unidad. Las semanas siguientes la heredan."""
        self.assertEqual(self.por_titulo["S1- Ejercitación Población y Muestra, Variables, Escalas"]["unidad"], 1)
        self.assertEqual(self.por_titulo["S2-Ejercitación: interpretación de tablas de frecuencias"]["unidad"], 1)
        self.assertEqual(self.por_titulo["S5-Cuestionario: técnicas de conteo"]["unidad"], 2)
        self.assertEqual(self.por_titulo["S10- Ejercitación de Modelo Binomial"]["unidad"], 3)

    def test_la_semana_de_repaso_no_HEREDA_pero_el_titulo_explicito_gana(self):
        """La semana 8 es "Repaso Unidades N° 1 y 2" y la 9 el parcial: ninguna de las dos
        ES de una unidad, así que no heredan la última abierta (que sería la 2) — eso metía
        el Primer Parcial adentro de la unidad 2.

        Pero la precedencia de siempre no se suspende: si el título DECLARA la unidad, gana
        el título. "S8-Cuestionario 1, revisión Unidad 1" es de la unidad 1 y lo dice él
        mismo; el parcial no dice nada y se queda sin unidad, que es lo honesto.
        """
        self.assertEqual(self.por_titulo["S8-Cuestionario 1, revisión Unidad 1"]["unidad"], 1)
        self.assertIsNone(self.por_titulo["Primer Examen Parcial"]["unidad"])

    def test_un_cuestionario_es_cadencia_y_no_una_instancia_aparte(self):
        """EL bug de `tio`: "cues-tio-nario" caía en la lista de exclusiones y los 36
        cuestionarios de la materia quedaban fuera de su propia cadencia."""
        self.assertEqual(
            self.por_titulo["S5-Cuestionario: técnicas de conteo"]["naturaleza"],
            calificador.CADENCIA)
        self.assertFalse(
            self.por_titulo["S5-Cuestionario: técnicas de conteo"]["fuera_de_cadencia"])

    def test_el_parcial_es_evaluacion_y_el_TAI_integrador(self):
        self.assertEqual(self.por_titulo["Primer Examen Parcial"]["naturaleza"],
                         calificador.EVALUACION)
        self.assertEqual(
            self.por_titulo["Primera entrega del TAI – Consignas 1 y 2"]["naturaleza"],
            calificador.INTEGRADOR)

    def test_las_columnas_de_nota_no_son_entregas_del_alumno(self):
        """"CONDICIONES FINALES" y "Calificación Coloquio" son casillas donde el docente
        vuelca una nota: ningún alumno entrega nada ahí. Contarlas como entregas le muestra
        al coordinador tres actividades que nadie hizo y nadie va a hacer. Y por el título
        solo no alcanza: "Calificación Coloquio" se leería como el coloquio."""
        for t in ("CONDICIONES FINALES", "Calificación Coloquio"):
            self.assertEqual(self.por_titulo[t]["naturaleza"], calificador.ADMINISTRATIVA, t)

    def test_hasta_donde_llego_el_curso_sale_del_dato(self):
        """El campus no dice por qué semana va la cursada: 32 de los 36 cuestionarios no
        tienen fecha de cierre. Sin esto el informe muestra 16 semanas donde 11 están
        vacías por CALENDARIO y se leen como abandono."""
        filas = [{"ultima_con_actividad": 5}, {"ultima_con_actividad": 2},
                 {"ultima_con_actividad": None}]
        self.assertEqual(calificador.hasta_donde_llego_el_curso(filas), 5)
        self.assertIsNone(calificador.hasta_donde_llego_el_curso(
            [{"ultima_con_actividad": None}]))


class TestGruposDeComision(unittest.TestCase):
    """Qué grupo es una comisión. Estaba escrito DOS veces y las dos daban CERO en
    Probabilidad y Estadística, que las llama "Comisión 7" y no "A26 C1-07"."""

    def test_los_dos_vocabularios(self):
        self.assertEqual(grupos.etiqueta("A26 C1-06"), "com6")
        self.assertEqual(grupos.etiqueta("M26 C2-14"), "com14")
        self.assertEqual(grupos.etiqueta("Comisión 7"), "com7")
        self.assertEqual(grupos.etiqueta("Comision 15"), "com15")
        self.assertEqual(grupos.etiqueta("COMISIÓN N° 3"), "com3")

    def test_lo_que_NO_es_una_comision(self):
        """Los tres grupos que conviven con las comisiones en el aula de PyE. Meterlos
        infla el padrón con gente que no cursa y con grupos que no son de nadie."""
        for n in ("Coloquio Final Integrador", "Tutores Académicos", "R-Rosario",
                  "Inscripción a Comisiones", "Comisión 1 - Coloquio", "", "Comisiones"):
            self.assertIsNone(grupos.etiqueta(n), n)

    def test_las_regionales_se_reconocen_en_cualquier_caja(self):
        """La copia de `ws_api` comparaba en mayúsculas y la de `panorama` no: "r-córdoba"
        era regional para una mitad de la skill y "otro" para la otra."""
        self.assertEqual(grupos.regional_de("R-Rosario"), "Rosario")
        self.assertEqual(grupos.clasificar("r-córdoba"), grupos.REGIONAL)
        self.assertEqual(grupos.clasificar("Grupo_2"), grupos.OTRO)


def _bloque(com, notas_por_alumno, tutor="Docente"):
    """Una comisión armada a mano: `notas_por_alumno` es una lista de sets de item_id."""
    alumnos = []
    for i, ids in enumerate(notas_por_alumno):
        alumnos.append({
            "userid": 1000 + i, "nombre": f"ALUMNO {com}-{i}",
            "notas": {j: {"nota": 10} for j in ids},
            "por_eje": {5: {"hechas": 1 if ids else 0, "total": 2}},
            "dias_sin_abrir_la_materia": 0,
            "sin_actividad": not ids,
        })
    return {"comision": com, "nombre": com, "tutor": {"nombre": tutor},
            "alumnos": alumnos,
            "resumen": {"alumnos": len(alumnos),
                        "con_actividad": sum(1 for a in alumnos if not a["sin_actividad"]),
                        "sin_actividad": sum(1 for a in alumnos if a["sin_actividad"]),
                        "nunca_abrieron_la_materia": 0,
                        "notas_cargadas": {"cuestionario": sum(len(a["notas"]) for a in alumnos)}}}


class TestVistaDelCoordinador(unittest.TestCase):
    """El corte por ACTIVIDAD, que ninguna vista por comisión da.

    El caso es real y salió del aula de Probabilidad y Estadística el 2026-09-03: el foro
    calificado de la semana 1 tenía 31 notas en la comisión 11, 26 en la 3 … y CERO en la
    4 y una en la 2. Por comisión eso se lee como "la com4 participa menos"; por actividad
    se ve que en esa comisión la actividad no tiene una sola nota mientras en las otras
    trece sí. Son dos conclusiones distintas y llevan a llamar a personas distintas.
    """

    def setUp(self):
        # item 1 = cuestionario que anda en todas; item 2 = foro que falta en com4
        self.items = {"items": [
            {"item_id": 1, "nro": 1, "titulo": "S1-Cuestionario", "tipo": "cuestionario",
             "naturaleza": "cadencia", "unidad": 1, "semana": 1, "cmid": 10},
            {"item_id": 2, "nro": 2, "titulo": "S1-Foro calificado", "tipo": "foro",
             "naturaleza": "cadencia", "unidad": 1, "semana": 1, "cmid": 11},
        ], "eje": "semana"}
        self.bloques = [
            _bloque("com1", [{1, 2}, {1, 2}, {1, 2}], "Biondi"),
            _bloque("com2", [{1, 2}, {1, 2}, {1}], "Isla Zuvialde"),
            _bloque("com3", [{1}, {1}, {1}], "Comerci"),      # el foro en CERO
            _bloque("com4", [{1, 2}, {1}, set()], "Figueroa"),
        ]

    def test_una_actividad_en_cero_en_una_comision_y_andando_en_el_resto_se_marca(self):
        por_act = calificador.panorama_por_actividad(self.bloques, self.items,
                                                     minimo_para_comparar=5)
        foro = next(f for f in por_act if f["nro"] == 2)
        self.assertEqual(foro["notas_en_el_curso"], 6)
        self.assertEqual(foro["comisiones_en_cero"], ["com3"])
        cuest = next(f for f in por_act if f["nro"] == 1)
        self.assertEqual(cuest["comisiones_en_cero"], [])

    def test_una_actividad_que_no_arranco_en_NINGUN_lado_no_es_un_hueco(self):
        """Catorce comisiones en cero sobre una actividad que el curso todavía no dictó no
        dicen nada. Marcarlas llena la lista de ruido y nadie la mira más."""
        bloques = [_bloque("com1", [{1}]), _bloque("com2", [{1}])]
        por_act = calificador.panorama_por_actividad(bloques, self.items,
                                                     minimo_para_comparar=5)
        foro = next(f for f in por_act if f["nro"] == 2)
        self.assertEqual(foro["notas_en_el_curso"], 0)
        self.assertEqual(foro["comisiones_en_cero"], [])

    def test_una_actividad_en_cero_en_TODAS_tampoco_es_un_hueco_de_una_comision(self):
        """Si falta en todas, el problema es del curso y no de ninguna comisión."""
        bloques = [_bloque("com1", [{1}] * 5), _bloque("com2", [{1}] * 5)]
        por_act = calificador.panorama_por_actividad(bloques, self.items,
                                                     minimo_para_comparar=1)
        foro = next(f for f in por_act if f["nro"] == 2)
        self.assertEqual(foro["comisiones_en_cero"], [])

    def test_los_huecos_se_ordenan_por_cuantas_comisiones_quedaron_afuera(self):
        """Cuantas más comisiones, menos se parece a un problema de una persona."""
        por_act = calificador.panorama_por_actividad(self.bloques, self.items,
                                                     minimo_para_comparar=5)
        huecos = calificador.huecos_de_calificacion(por_act)
        self.assertEqual([h["nro"] for h in huecos], [2])

    def test_la_fila_por_comision_lleva_al_tutor_y_no_un_puntaje(self):
        filas = calificador.panorama_por_comision(self.bloques, self.items, hasta=5)
        self.assertEqual(filas[0]["tutor"], "Biondi")
        self.assertEqual(filas[0]["alumnos"], 3)
        # `al_dia` es la única columna comparable, y sale del eje del curso
        self.assertEqual(filas[0]["al_dia"], 3)
        self.assertEqual(filas[3]["sin_actividad"], 1)

    def test_una_comision_vacia_lo_declara_en_vez_de_mostrar_cero(self):
        """Un 0 porque no se pudo leer el padrón y un 0 porque nadie hizo nada son cosas
        distintas: sólo una manda a llamar a alguien."""
        filas = calificador.panorama_por_comision(
            [{"comision": "com9", "alumnos": [], "resumen": {}}], self.items, hasta=5)
        self.assertTrue(filas[0]["sin_dato"])

    def test_los_alumnos_sin_comision_van_con_nombre(self):
        """Decir "hay 1" no le sirve a nadie: no lo ve ningún tutor, así que hay que poder
        escribirle."""
        padron = {"sueltos": [
            {"id": 77, "fullname": "SUELTO UNO", "email": "s1@x.com",
             "lastcourseaccess": 0, "lastaccess": 0}]}
        fuera = calificador.alumnos_sin_comision(padron, uids_en_comisiones={1, 2})
        self.assertEqual(len(fuera), 1)
        self.assertEqual(fuera[0]["nombre"], "SUELTO UNO")
        self.assertEqual(fuera[0]["email"], "s1@x.com")
        self.assertEqual(fuera[0]["estado_aula"], "nunca_abrio")

    def test_el_que_ya_esta_en_una_comision_no_se_cuenta_como_suelto(self):
        padron = {"sueltos": [{"id": 1, "fullname": "YA ESTA", "email": "y@x.com"}]}
        self.assertEqual(
            calificador.alumnos_sin_comision(padron, uids_en_comisiones={1}), [])


if __name__ == "__main__":
    unittest.main()
