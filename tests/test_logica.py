"""Tests de la lógica PURA de la skill: sin red, sin Moodle, sin credenciales.

Por qué acá y no en las funciones que consultan el campus: estas son las que deciden
**qué significa** un dato — si una nota es Aprobado, si un alumno está abandonando, si hay
una versión nueva. Todos los bugs serios encontrados hasta ahora vivían justo acá, y todos
se podían haber cazado sin tocar la red.

Correr:  .venv/bin/python -m unittest discover -s tests -v
Se usa `unittest` (stdlib) y no pytest a propósito: no agrega una dependencia que el tutor
tendría que instalar para poder verificar su propia herramienta.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

from moodle.ws_api import (  # noqa: E402
    _clasificar_riesgo,
    _racha_final_sin_entregar,
    _resolver_valor_nota,
    clasificar_mensaje_entrante,
    es_actividad_de_cierre,
    nota_display,
)
from moodle.version import _tupla, hay_novedad  # noqa: E402


class TestEscalaInvertida(unittest.TestCase):
    """La escala 5 de la TUP va INVERTIDA: 1=Aprobado, 2=Desaprobado.

    Es la trampa central del dominio: un número suelto se lee al revés de lo que significa.
    """

    def test_lee_la_escala_por_su_mapa(self):
        self.assertEqual(nota_display(-5, "1.00000"), "Aprobado")
        self.assertEqual(nota_display(-5, "2.00000"), "Desaprobado")

    def test_escala_desconocida_NO_devuelve_el_numero_crudo(self):
        # El bug: devolvía "1", que en cualquier otra escala se lee como la peor nota.
        r = nota_display(-7, "1.00000")
        self.assertNotIn(r.strip(), ("1", "1.0", "1.00000"))
        self.assertIn("desconocida", r.lower())

    def test_valor_fuera_de_la_escala_avisa(self):
        r = nota_display(-5, "9.00000")
        self.assertIn("9", r)
        self.assertIn("no está en la escala", r)

    def test_valor_ilegible_avisa(self):
        self.assertIn("ilegible", nota_display(-5, "chau").lower())

    def test_sin_nota_es_None_y_no_texto(self):
        # -1 es el "sin calificar" de Moodle. Confundirlo con una nota fue el bug que dejó
        # a tres alumnos como "calificados" sin calificación.
        for v in (None, "", "-1", "-1.00000"):
            self.assertIsNone(nota_display(-5, v), f"con {v!r}")

    def test_numerica_se_muestra_limpia(self):
        self.assertEqual(nota_display(100, "8.9"), "8.9")
        self.assertEqual(nota_display(100, "10.00000"), "10")


class TestResolverValorNota(unittest.TestCase):
    """Texto -> valor que espera save_grade. Es la puerta de la ESCRITURA de notas."""

    def test_acepta_el_texto_de_la_escala_sin_importar_mayusculas(self):
        for texto in ("Aprobado", "aprobado", "APROBADO"):
            valor, etiqueta, _ = _resolver_valor_nota(-5, texto)
            self.assertEqual(valor, 1.0)
            self.assertEqual(etiqueta, "Aprobado")

    def test_rechaza_una_nota_numerica_en_tarea_de_escala(self):
        # Mandar "10" a una tarea Aprobado/Desaprobado tiene que ser rechazado ANTES de
        # escribir: si llegara al campus, la nota queda sin calificar y el alumno figura
        # como corregido sin nota.
        valor, _, opciones = _resolver_valor_nota(-5, "10")
        self.assertIsNone(valor)
        self.assertIn("Aprobado", opciones)

    def test_numerica_acepta_coma_y_punto(self):
        self.assertEqual(_resolver_valor_nota(100, "9,85")[0], 9.85)
        self.assertEqual(_resolver_valor_nota(100, "9.85")[0], 9.85)


class TestRachaSinEntregar(unittest.TestCase):
    """La racha se cuenta desde la ÚLTIMA tarea, no sobre el total."""

    def test_cuenta_solo_las_del_final(self):
        estados = ["Sin entrega", "Calificado", "Calificado", "Sin entrega", "Sin entrega"]
        self.assertEqual(_racha_final_sin_entregar(estados), 2)

    def test_quien_se_puso_al_dia_no_arrastra_la_deuda_vieja(self):
        # No entregó las dos primeras y después entregó todo: NO está abandonando.
        estados = ["Sin entrega", "Sin entrega", "Calificado", "Calificado"]
        self.assertEqual(_racha_final_sin_entregar(estados), 0)

    def test_todo_sin_entregar(self):
        self.assertEqual(_racha_final_sin_entregar(["Sin entrega"] * 4), 4)

    def test_lista_vacia(self):
        self.assertEqual(_racha_final_sin_entregar([]), 0)

    def test_enviado_sin_corregir_cuenta_como_entregado(self):
        # Que el tutor no haya corregido no es culpa del alumno.
        self.assertEqual(
            _racha_final_sin_entregar(["Sin entrega", "Enviado para calificar"]), 0)


class TestClasificarRiesgo(unittest.TestCase):
    def test_al_dia_es_verde(self):
        nivel, _ = _clasificar_riesgo(dias_sin_entrar=1, racha=0)
        self.assertEqual(nivel, "verde")

    def test_dos_tareas_seguidas_sin_entregar_es_rojo_aunque_entre_todos_los_dias(self):
        # La señal fuerte: entrar al campus sin entregar no es estar al día.
        nivel, motivos = _clasificar_riesgo(dias_sin_entrar=0, racha=2)
        self.assertEqual(nivel, "rojo")
        self.assertTrue(any("seguidas" in m for m in motivos))

    def test_muchos_dias_sin_entrar_pero_al_dia_con_las_tareas_es_amarillo(self):
        # Puede estar trabajando; no es abandono todavía.
        nivel, _ = _clasificar_riesgo(dias_sin_entrar=20, racha=0)
        self.assertEqual(nivel, "amarillo")

    def test_muchos_dias_sin_entrar_mas_una_sin_entregar_es_rojo(self):
        nivel, _ = _clasificar_riesgo(dias_sin_entrar=20, racha=1)
        self.assertEqual(nivel, "rojo")

    def test_una_semana_sin_entrar_es_amarillo(self):
        self.assertEqual(_clasificar_riesgo(dias_sin_entrar=8, racha=0)[0], "amarillo")

    def test_nunca_entro_es_rojo_y_no_verde(self):
        # `None` = nunca entró. Tratarlo como "sin datos" perdería justo al que más ayuda
        # necesita.
        nivel, motivos = _clasificar_riesgo(dias_sin_entrar=None, racha=0)
        self.assertEqual(nivel, "rojo")
        self.assertTrue(any("nunca entró" in m for m in motivos))

    def test_los_umbrales_son_configurables(self):
        self.assertEqual(_clasificar_riesgo(10, 0, dias_alerta=30, dias_aviso=20)[0], "verde")
        self.assertEqual(_clasificar_riesgo(10, 0, dias_alerta=5, dias_aviso=3)[0], "amarillo")


class TestQueCuentaParaLaRacha(unittest.TestCase):
    """Sólo las actividades de cierre de unidad marcan la cadencia semanal.

    Medido en vivo: 13 de 16 alumnos de una comisión no habían entregado el Integrador
    (grupal, una sola entrega al final). Contarlo en la racha marcaba en amarillo a gente
    que venía entrando todos los días y entregando todo — ruido que hace que después nadie
    mire el tablero.
    """

    def test_las_actividades_de_cierre_cuentan(self):
        for t in ("Actividad de cierre unidad 1 - Estructuras Secuenciales 🎯🏁",
                  "Actividad de cierre de la unidad 4 - Git 🎯🏁",
                  "Actividad de cierre unidad 10 - Recursividad 🎯🏁"):
            self.assertTrue(es_actividad_de_cierre(t), t)

    def test_el_integrador_y_los_parciales_NO_cuentan(self):
        for t in ("📤 Entrega del Trabajo Integrador 1",
                  "Recuperatorio Entrega del Trabajo Integrador",
                  "Parcial 2 - Programación 1: Sistema de Control de Inventario",
                  "Entrega - Extraordinaria Segundo Examen Parcial"):
            self.assertFalse(es_actividad_de_cierre(t), t)

    def test_tolera_acentos_y_mayusculas(self):
        self.assertTrue(es_actividad_de_cierre("ACTIVIDAD DE CIERRE UNIDAD 3"))
        self.assertTrue(es_actividad_de_cierre("actividad de cierre unidad 3"))


class TestClasificarMensaje(unittest.TestCase):
    """Qué mensaje espera respuesta de verdad. Textos reales de la bandeja del tutor.

    Antes se contaba como pendiente cualquier conversación donde el último en hablar fuera
    el alumno: 18 "esperando respuesta" cuando las reales eran ~3. Un contador que exagera
    seis veces se deja de mirar, y ahí se pierden las consultas que sí importan.
    """

    def test_agradecimientos_no_esperan_respuesta(self):
        for t in ("Muchas gracias 😊", "gracias!!", "okey muchas gracias Juan.",
                  "Genial, gracias!",
                  "Muchas gracias Juan!! Y gracias por tu ayuda durante toda la cursada! Saludos"):
            self.assertEqual(clasificar_mensaje_entrante(t), "cortesia", t)

    def test_agradecimiento_largo_con_saludo_adelante_igual_es_cortesia(self):
        # El saludo va primero y la cortesía después: exigir que arranque agradeciendo
        # dejaba este mensaje clasificado como consulta.
        t = ("Buenas tardes Juan! muchas gracias por todo, por el asesoramiento y estar "
             "siempre atento a lo que podia necesitar! La verdad que disfrute mucho la "
             "cursada, asique esperemos que programación II nos encuentre con la misma "
             "dinámica. Gracias nuevamente y abrazo grande!!")
        self.assertEqual(clasificar_mensaje_entrante(t), "cortesia")

    def test_gracias_SEGUIDO_DE_UNA_DUDA_es_pregunta(self):
        # El orden de evaluación importa: primero se busca si pide algo. Al revés, este
        # alumno se quedaba sin respuesta.
        self.assertEqual(
            clasificar_mensaje_entrante("muchas gracias! pero me quedó una duda con el TP"),
            "pregunta")

    def test_consultas_reales_son_pregunta(self):
        for t in ("Hola Juan buenas tardes, queria consultar si está la posibilidad de volver",
                  "Como hago para entregar el TP?",
                  "Buenas tardes Juan!! voy a rendir el final y te queria consultar la modalidad"):
            self.assertEqual(clasificar_mensaje_entrante(t), "pregunta", t)

    def test_anuncios_masivos_no_son_consultas(self):
        # Llegan por privado y figuran como mensaje "del alumno", pero nadie espera que el
        # tutor los conteste.
        for t in ("📢 Estimados estudiantes de Programación 2: Les recordamos que hoy vence",
                  "Hola! Les escribimos para recordarles las condiciones obligatorias"):
            self.assertEqual(clasificar_mensaje_entrante(t), "difusion", t)

    def test_ante_la_duda_entra_a_la_lista(self):
        # Default conservador: perder una consulta es peor que mostrar un mensaje de más.
        for t in ("llegue", "", "ya que virtual se me dificulta"):
            self.assertEqual(clasificar_mensaje_entrante(t), "pregunta", repr(t))


class TestVersionSemver(unittest.TestCase):
    def test_compara_por_numero_y_no_por_texto(self):
        # Como texto, "1.9" > "1.10". Es el bug clásico de comparar versiones.
        self.assertTrue(hay_novedad("1.9.0", "1.10.0"))
        self.assertFalse(hay_novedad("1.10.0", "1.9.0"))

    def test_misma_version_no_es_novedad(self):
        self.assertFalse(hay_novedad("1.2.0", "1.2.0"))

    def test_sin_dato_remoto_no_inventa_una_novedad(self):
        for remota in ("", "desconocida"):
            self.assertFalse(hay_novedad("1.0.0", remota))

    def test_tupla_tolera_basura(self):
        self.assertEqual(_tupla("1.2.3"), (1, 2, 3))
        self.assertEqual(_tupla("1.2.x"), (1, 2, 0))


class TestErroresFrecuentes(unittest.TestCase):
    """Bitácora de correcciones: de "este alumno se equivocó" a "esto no se explicó bien".

    Usa una base temporal (`MOODLE_SKILL_HOME`), así que no toca los datos del tutor ni
    necesita red.
    """

    @classmethod
    def setUpClass(cls):
        import asyncio
        import importlib
        import os
        import tempfile

        cls._tmp = tempfile.mkdtemp()
        cls._home_previo = os.environ.get("MOODLE_SKILL_HOME")
        os.environ["MOODLE_SKILL_HOME"] = cls._tmp
        from moodle import almacen
        importlib.reload(almacen)          # relee HOME/DB_PATH del entorno nuevo
        cls.almacen = almacen
        # OJO: el helper NO puede llamarse `run` — ese es el método con el que unittest
        # ejecuta cada test, y pisarlo rompe la corrida entera.
        cls.correr = staticmethod(asyncio.run)
        asyncio.run(almacen.init_db())

    @classmethod
    def tearDownClass(cls):
        import os
        if cls._home_previo is None:
            os.environ.pop("MOODLE_SKILL_HOME", None)
        else:
            os.environ["MOODLE_SKILL_HOME"] = cls._home_previo

    def _corregir(self, alumno, etiquetas):
        self.correr(self.almacen.guardar_correccion({
            "course_id": 74, "assign_id": "17703", "tarea": "u1", "comision": "com6",
            "email": f"{alumno}@x.com", "alumno": alumno, "nota": "Aprobado",
            "devolucion": "…", "etiquetas": etiquetas}))

    def test_arranca_vacio_sin_inventar_nada(self):
        # Agosto empieza de cero: sin correcciones no hay temas, y eso es correcto.
        r = self.correr(self.almacen.errores_frecuentes(assign_id="no-existe"))
        self.assertEqual(r["correcciones_registradas"], 0)
        self.assertEqual(r["temas"], [])

    def test_marca_sistemico_lo_que_le_pasa_a_muchos(self):
        for a, e in (("ana", ["perimetro"]), ("beto", ["perimetro"]),
                     ("caro", ["perimetro"]), ("dani", []), ("eve", ["otro-tema"])):
            self._corregir(a, e)
        r = self.correr(self.almacen.errores_frecuentes(course_id=74))
        temas = {t["tema"]: t for t in r["temas"]}
        # 3 de 5 = 60% -> el problema dejó de ser individual
        self.assertEqual(temas["perimetro"]["alumnos_afectados"], 3)
        self.assertEqual(temas["perimetro"]["porcentaje"], 60)
        self.assertTrue(temas["perimetro"]["sistemico"])
        # 1 de 5 = 20% -> caso puntual
        self.assertFalse(temas["otro-tema"]["sistemico"])

    def test_el_porcentaje_es_sobre_los_CORREGIDOS_no_sobre_los_afectados(self):
        # Lo que importa no es "3 se equivocaron" sino "3 de 5": sin el denominador el
        # número no dice si hay que rehacer la clase o hablar con una persona.
        r = self.correr(self.almacen.errores_frecuentes(course_id=74))
        for t in r["temas"]:
            self.assertEqual(t["de_corregidos"], r["correcciones_registradas"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
