"""Tests del lector de títulos de actividad (`moodle/titulos.py`).

Por qué existe este archivo: el criterio de "qué unidad es esta actividad" estaba
escrito TRES veces (la racha de abandono, la columna RETRASO del informe de
coordinación y las columnas del panel), ninguna tenía un solo test, y las tres
daban CERO al mapear Matemática — que nombra su cadencia `ENTREGA U3S1` y no usa
la palabra "unidad" ni "cierre" en ningún lado.

El caso que mandó a arreglar esto está en `test_el_cuadernillo_no_es_la_unidad`:
no fallaba con un error, fallaba devolviendo un número plausible y equivocado.

Correr:  .venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from moodle import titulos  # noqa: E402
from moodle.panorama import cierres_por_unidad  # noqa: E402

# Títulos REALES, traídos del campus el 2026-09-02. No son inventados a propósito:
# el bug que motivó el módulo era invisible con ejemplos limpios.
MATEMATICA = [
    "ENTREGA U1S1: Ejercicio 8 de la Práctica 1",
    "ENTREGA U1S2: Ejercicio 2 de la Práctica 2",
    "ENTREGA U2S1: Ejercicio 8 de la Práctica 1",
    "ENTREGA U3S1: Ejercicio 3, 4 y 12 de la Práctica 1",
    "ENTREGA U4S1: Ejercicio 9) - Semana 1- Conjuntos",
    "ENTREGA U5S3: Ejercicio semana 3 - Funciones y Rectas",
    "ENTREGA U6S2: Ejercicio 4 de la Práctica 2",
]
PROGRAMACION = [
    "Actividad de cierre unidad 1 - Estructuras Secuenciales",
    "Actividad de cierre de la unidad 2 -  Estructuras Condicionales",
    "Actividad de cierre unidad 10 - Recursividad 🎯🏁",
    "📤 Entrega del Trabajo Integrador",
]


class LeerTitulo(unittest.TestCase):
    def test_el_cuadernillo_no_es_la_unidad(self):
        """EL bug. "U2S1 ... de la Práctica 1" es la unidad 2, no la 1.

        La versión vieja buscaba la palabra "práctica" y se quedaba con SU número,
        así que las unidades 1, 2, 3 y 6 devolvían todas `U1`. No reventaba: devolvía
        un número creíble. En la grilla se leía como cuatro actividades de la unidad 1.
        """
        act = titulos.leer("ENTREGA U2S1: Ejercicio 8 de la Práctica 1")
        self.assertEqual(act.unidad, 2)
        self.assertEqual(act.semana, 1)
        self.assertEqual(act.etiqueta, "U2S1")

    def test_dos_vocabularios(self):
        self.assertEqual(titulos.nro_unidad("Actividad de cierre unidad 4 - Git 🎯🏁"), 4)
        self.assertEqual(titulos.nro_unidad("ENTREGA U4S2: Ejercicio 9) - Conjuntos"), 4)

    def test_unidad_de_dos_digitos(self):
        self.assertEqual(titulos.nro_unidad("Actividad de cierre unidad 10 - Recursividad"), 10)

    def test_sin_unidad_devuelve_none_no_cero(self):
        """None y 0 son cosas distintas: "no lo dice" no es "la unidad cero"."""
        act = titulos.leer("Entrega trabajo 1")
        self.assertIsNone(act.unidad)
        self.assertIsNone(act.etiqueta)

    def test_la_semana_no_se_inventa(self):
        """Programación no tiene semanas. La etiqueta no debe fabricar una."""
        act = titulos.leer("Actividad de cierre unidad 3 - Estructuras Repetitivas")
        self.assertIsNone(act.semana)
        self.assertEqual(act.etiqueta, "U3")


class Cadencia(unittest.TestCase):
    def test_matematica_entera_es_cadencia(self):
        for t in MATEMATICA:
            self.assertTrue(titulos.es_actividad_de_cierre(t), t)

    def test_programacion_sigue_igual(self):
        for t in PROGRAMACION[:3]:
            self.assertTrue(titulos.es_actividad_de_cierre(t), t)

    def test_integrador_y_parcial_quedan_afuera(self):
        """Tienen calendario propio: contarlos en la racha marcaba en amarillo a
        gente que venía entregando todo al día."""
        for t in ("📤 Entrega del Trabajo Integrador",
                  "Parcial de la unidad 5",
                  "Recuperatorio unidad 2"):
            self.assertFalse(titulos.es_actividad_de_cierre(t), t)

    def test_practica_suelta_no_cuenta(self):
        """Las "Práctica - Actividad N" de Prog III son optativas: sumarlas marcaría
        retrasado a medio curso por no hacer ejercicios sueltos."""
        self.assertFalse(titulos.es_actividad_de_cierre("Práctica - Actividad 3"))


class Orden(unittest.TestCase):
    def test_ordena_por_unidad_y_despues_semana(self):
        ordenado = sorted(MATEMATICA, key=titulos.orden)
        self.assertEqual([titulos.etiqueta(t) for t in ordenado],
                         ["U1S1", "U1S2", "U2S1", "U3S1", "U4S1", "U5S3", "U6S2"])

    def test_el_integrador_va_despues_de_las_unidades(self):
        ordenado = sorted(PROGRAMACION, key=titulos.orden)
        self.assertEqual(ordenado[-1], "📤 Entrega del Trabajo Integrador")

    def test_u10_va_despues_de_u2_y_no_alfabetico(self):
        """Ordenar por texto pondría U10 entre U1 y U2."""
        ordenado = sorted(
            ["Actividad de cierre unidad 10 - Recursividad 🎯🏁",
             "Actividad de cierre unidad 2 - Estructuras Condicionales"],
            key=titulos.orden)
        self.assertEqual(titulos.nro_unidad(ordenado[0]), 2)


class CierresPorUnidad(unittest.TestCase):
    def test_con_varias_semanas_gana_la_ultima(self):
        """La que cierra la unidad es la última semana. Medir el retraso contra la
        primera daría por atrasado a quien viene al día en la semana que corre."""
        meta = {
            "1": {"titulo": "ENTREGA U5S1: Ejercicio 9 semana 1 de Matrices"},
            "2": {"titulo": "ENTREGA U5S2: Ejercicio semana 2 de Matrices"},
            "3": {"titulo": "ENTREGA U5S3: Ejercicio semana 3 - Funciones y Rectas"},
        }
        cierres = cierres_por_unidad(meta)
        self.assertEqual(list(cierres), [5])
        self.assertEqual(cierres[5]["semana"], 3)
        self.assertEqual(cierres[5]["cmid"], "3")

    def test_programacion_una_por_unidad(self):
        meta = {str(i): {"titulo": t} for i, t in enumerate(PROGRAMACION[:3])}
        self.assertEqual(sorted(cierres_por_unidad(meta)), [1, 2, 10])

    def test_el_integrador_no_es_cierre_de_ninguna_unidad(self):
        meta = {"1": {"titulo": "📤 Entrega del Trabajo Integrador"}}
        self.assertEqual(cierres_por_unidad(meta), {})


if __name__ == "__main__":
    unittest.main()
