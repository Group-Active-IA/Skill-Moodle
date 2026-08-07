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

from moodle.cliente import MoodleWSError  # noqa: E402
from moodle.ws_api import (  # noqa: E402
    _a_html,
    _clasificar_riesgo,
    _racha_final_sin_entregar,
    _resolver_valor_nota,
    clasificar_mensaje_entrante,
    crear_discusion,
    es_actividad_de_cierre,
    nota_display,
)
from moodle import panorama  # noqa: E402
from moodle.active_ia import _nota_de_entrega  # noqa: E402
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

    def test_con_muestra_chica_NO_marca_sistemico(self):
        # Apareció corrigiendo de verdad: con 2 corregidos, 1 error da 50% y la tool decía
        # "conviene reforzar el tema con toda la comisión" por UNA sola persona. Un
        # porcentaje sobre 2 casos no significa nada.
        import tempfile
        from moodle import almacen as alm
        prev_db = alm.DB_PATH
        alm.DB_PATH = f"{tempfile.mkdtemp()}/chica.db"
        try:
            self.correr(alm.init_db())
            for a in ("uno", "dos"):
                self.correr(alm.guardar_correccion({
                    "course_id": 99, "assign_id": "x", "comision": "c", "email": f"{a}@x",
                    "alumno": a, "nota": "Aprobado", "devolucion": "",
                    "etiquetas": ["tema-x"] if a == "uno" else []}))
            r = self.correr(alm.errores_frecuentes(course_id=99))
            self.assertEqual(r["correcciones_registradas"], 2)
            self.assertFalse(r["muestra_suficiente"])
            self.assertEqual(r["temas"][0]["porcentaje"], 50)      # el % se calcula igual
            self.assertFalse(r["temas"][0]["sistemico"])           # pero NO concluye
        finally:
            alm.DB_PATH = prev_db

    def test_el_porcentaje_es_sobre_los_CORREGIDOS_no_sobre_los_afectados(self):
        # Lo que importa no es "3 se equivocaron" sino "3 de 5": sin el denominador el
        # número no dice si hay que rehacer la clase o hablar con una persona.
        r = self.correr(self.almacen.errores_frecuentes(course_id=74))
        for t in r["temas"]:
            self.assertEqual(t["de_corregidos"], r["correcciones_registradas"])

    def test_recargar_una_nota_NO_cuenta_al_alumno_dos_veces(self):
        # Recargar una nota —para arreglar la devolución, o tras un fallo— deja OTRA fila
        # en la bitácora. Contando filas, 6 alumnos cargados dos veces informaban "12
        # correcciones". Lo grave no es el número inflado: al superar así la muestra
        # mínima, la tool marcaba `sistemico` sin evidencia — y ese es el dato con el que
        # se decide rehacer material de cátedra.
        import tempfile
        from moodle import almacen as alm
        prev_db = alm.DB_PATH
        alm.DB_PATH = f"{tempfile.mkdtemp()}/dedup.db"
        try:
            self.correr(alm.init_db())
            for _ in range(2):                     # cada alumno, cargado DOS veces
                for a in ("ana", "beto", "caro", "dani", "eve", "fer"):
                    self.correr(alm.guardar_correccion({
                        "course_id": 7, "assign_id": "u1", "comision": "c",
                        "email": f"{a}@x", "alumno": a, "nota": "Aprobado",
                        "devolucion": "", "etiquetas": ["tema-x"] if a == "ana" else []}))
            r = self.correr(alm.errores_frecuentes(course_id=7))
            self.assertEqual(r["correcciones_registradas"], 6)   # 6 alumnos, no 12 filas
            temas = {t["tema"]: t for t in r["temas"]}
            self.assertEqual(temas["tema-x"]["alumnos_afectados"], 1)   # 1 persona, no 2
            self.assertFalse(temas["tema-x"]["sistemico"])
        finally:
            alm.DB_PATH = prev_db


class TestEscala3(unittest.TestCase):
    """Escala 3 (Prog IV): No satisfactorio / Satisfactorio / Supera lo esperado.

    Relevada del `<select>` del grader real el 2026-08-04. Antes devolvía "escala
    desconocida" y `cargar_nota` pedía un número para un desplegable de tres opciones.
    """

    def test_lee_las_tres_etiquetas(self):
        self.assertEqual(nota_display(-3, "1.00000"), "No satisfactorio")
        self.assertEqual(nota_display(-3, "2.00000"), "Satisfactorio")
        self.assertEqual(nota_display(-3, "3.00000"), "Supera lo esperado")

    def test_satisfactorio_NO_matchea_dentro_de_no_satisfactorio(self):
        # "satisfactorio" es substring de "no satisfactorio". Si el match fuera por
        # inclusión en vez de exacto, un "No satisfactorio" se guardaría como Satisfactorio
        # — el alumno aprobado por un substring. Este test fija que el match es exacto.
        self.assertEqual(_resolver_valor_nota(-3, "No satisfactorio")[:2],
                         (1.0, "No satisfactorio"))
        self.assertEqual(_resolver_valor_nota(-3, "satisfactorio")[:2],
                         (2.0, "Satisfactorio"))

    def test_esta_escala_NO_va_invertida_y_la_5_si(self):
        # Las dos conviven en el mismo campus, así que no hay una regla general: cada
        # escala se releva del grader. Hardcodear "1 es la peor" rompe la 5.
        self.assertEqual(nota_display(-3, "1.00000"), "No satisfactorio")   # 1 = la peor
        self.assertEqual(nota_display(-5, "1.00000"), "Aprobado")           # 1 = la mejor


class TestNoSeSiEsEscalaONumerica(unittest.TestCase):
    """Ante la duda NO se adivina.

    Antes, un `grade_cfg` ilegible caía a `0` = numérica — y numérica es justamente el modo
    que corrompe: en la escala 5 un "1" pensado como la peor nota guarda APROBADO.
    """

    def test_no_resuelve_la_nota_si_no_sabe_el_tipo(self):
        for gc in (None, "", "chau"):
            self.assertIsNone(_resolver_valor_nota(gc, "Aprobado")[0],
                              f"con grade_cfg={gc!r} resolvió igual")

    def test_tampoco_acepta_un_numero_a_ciegas(self):
        # El que importa: antes, con el tipo ilegible, un 9.5 se escribía como numérica sin
        # chistar. Si la tarea era de escala, eso queda mal en el legajo de una persona.
        self.assertIsNone(_resolver_valor_nota(None, "9.5")[0])

    def test_nota_display_avisa_en_vez_de_inventar(self):
        r = nota_display(None, "1.00000")
        self.assertNotIn(r.strip(), ("1", "1.0", "1.00000"))
        self.assertIn("no sé", r.lower())


class TestNotaDeEntregaActiveIA(unittest.TestCase):
    """Una nota 0 es una nota.

    `/entregas/` devuelve la nota bajo tres nombres posibles según el caso. Encadenarlos
    con `or` hacía que un 0 —entrega vacía, plagio— cayera hasta `None` y la entrega
    desapareciera de `activeia_correcciones` como si nunca se hubiera corregido.
    """

    def test_cero_no_desaparece(self):
        self.assertEqual(_nota_de_entrega({"nota": 0}), 0)
        self.assertEqual(_nota_de_entrega({"puntaje": 0.0}), 0.0)

    def test_el_primero_que_EXISTE_gana_aunque_sea_cero(self):
        # Con el `or` encadenado esto devolvía 99: el 0 real se perdía y se reportaba la
        # nota de otro campo.
        self.assertEqual(_nota_de_entrega({"nota": 0, "calificacion": 99}), 0)

    def test_cae_al_campo_siguiente_solo_si_el_anterior_FALTA(self):
        self.assertEqual(_nota_de_entrega({"calificacion": 87}), 87)
        self.assertEqual(_nota_de_entrega({"nota": None, "puntaje": 70}), 70)

    def test_sin_ningun_campo_es_None(self):
        self.assertIsNone(_nota_de_entrega({"alumno_nombre": "x"}))


class TestAvisoAHtml(unittest.TestCase):
    """El aviso del foro se guarda con messageformat=1, o sea HTML."""

    def test_cada_parrafo_en_su_p_y_el_salto_simple_es_br(self):
        # Sin esto el campus se come los saltos y el aviso queda como un ladrillo.
        self.assertEqual(_a_html("uno\n\ndos"), "<p>uno</p><p>dos</p>")
        self.assertEqual(_a_html("uno\ndos"), "<p>uno<br>dos</p>")

    def test_escapa_el_codigo(self):
        # Es un foro de PROGRAMACIÓN: sin escapar, un aviso que diga "if (a < b)" o
        # "List<int>" se renderiza mutilado y el tutor se entera cuando ya está publicado.
        self.assertEqual(_a_html("if (a < b)"), "<p>if (a &lt; b)</p>")
        self.assertIn("&amp;&amp;", _a_html("a && b"))
        self.assertNotIn("<int>", _a_html("List<int>"))

    def test_vacio_no_rompe(self):
        for v in ("", "   ", "\n\n", None):
            self.assertEqual(_a_html(v), "", f"con {v!r}")


class ClienteFalso:
    """Doble mínimo del cliente Moodle: devuelve lo que le pusiste, por función."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.llamadas = []

    async def ws(self, fn, params=None):
        self.llamadas.append(fn)
        r = self.respuestas.get(fn)
        if isinstance(r, Exception):
            raise r
        return r


class TestCrearDiscusionNoPublicaAlCursoEntero(unittest.TestCase):
    """Publicar un aviso de comisión a cientos de alumnos ajenos NO se deshace desde la
    API. Por eso acá "no sé a cuántos llega" tiene que ser un freno, no un cartel.
    """

    FORO_CON_GRUPOS = {
        "core_course_get_course_module_by_instance": {
            "cm": {"id": 1, "name": "Avisos de la comisión", "course": 74, "groupmode": 1}},
        "mod_forum_can_add_discussion": {"status": True},
        "core_group_get_course_groups": [{"id": 7740, "name": "A26 C1-6"}],
        "core_enrol_get_enrolled_users": [
            {"id": 1, "roles": [{"shortname": "student"}]},
            {"id": 2, "roles": [{"shortname": "student"}]},
            {"id": 3, "roles": [{"shortname": "editingteacher"}]},   # el tutor no cuenta
        ],
        "mod_forum_add_discussion": {"discussionid": 999},
    }

    def _correr(self, respuestas, **kw):
        import asyncio
        cli = ClienteFalso(respuestas)
        r = asyncio.run(crear_discusion(cli, 55, "Aviso", "hola", **kw))
        return r, cli

    def test_foro_con_grupos_sin_group_id_NO_publica_aunque_confirmes(self):
        # EL test. El freno no puede vivir sólo en el preview: si alguien llama directo
        # con confirmado=true, tiene que seguir frenando igual.
        r, cli = self._correr(self.FORO_CON_GRUPOS, confirmado=True)
        self.assertIn("error", r)
        self.assertIn("GRUPOS SEPARADOS", r["error"])
        self.assertNotIn("mod_forum_add_discussion", cli.llamadas)

    def test_si_no_puede_leer_el_foro_NO_publica(self):
        # Antes, si esta llamada fallaba el contexto quedaba vacío, la alerta no se
        # disparaba y la tool publicaba callada. Justo cuando menos se sabe.
        resp = dict(self.FORO_CON_GRUPOS)
        resp["core_course_get_course_module_by_instance"] = MoodleWSError(
            "core_course_get_course_module_by_instance", {"errorcode": "accessexception"})
        r, cli = self._correr(resp, confirmado=True)
        self.assertIn("error", r)
        self.assertNotIn("mod_forum_add_discussion", cli.llamadas)
        self.assertTrue(r["_meta"]["degradado"])

    def test_group_id_inexistente_no_se_publica_en_otro_lado(self):
        r, cli = self._correr(self.FORO_CON_GRUPOS, group_id=9999, confirmado=True)
        self.assertIn("no existe", r["error"])
        self.assertNotIn("mod_forum_add_discussion", cli.llamadas)

    def test_con_group_id_valido_el_preview_dice_a_cuantos_llega(self):
        r, _ = self._correr(self.FORO_CON_GRUPOS, group_id=7740)
        self.assertTrue(r["requiere_confirmacion"])
        destino = r["preview"]["destino"]
        self.assertEqual(destino["grupo_nombre"], "A26 C1-6")
        self.assertEqual(destino["alcance_alumnos"], 2)      # el editingteacher no cuenta
        self.assertEqual(destino["alcance"], "comisión")

    def test_group_id_cero_EXPLICITO_es_una_decision_y_se_permite(self):
        # 0 no es "no me lo dijiste": es "quiero que lo vea el curso entero".
        r, cli = self._correr(self.FORO_CON_GRUPOS, group_id=0, confirmado=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["alcance"], "curso entero")
        self.assertIn("mod_forum_add_discussion", cli.llamadas)

    def test_no_contar_alumnos_no_es_contar_cero(self):
        resp = dict(self.FORO_CON_GRUPOS)
        resp["core_enrol_get_enrolled_users"] = MoodleWSError(
            "core_enrol_get_enrolled_users", {"errorcode": "accessexception"})
        r, _ = self._correr(resp, group_id=7740)
        self.assertIsNone(r["preview"]["destino"]["alcance_alumnos"])
        self.assertTrue(any("desconocido" in a for a in r["_meta"]["avisos"]))


class TestPanoramaClasificaEntregas(unittest.TestCase):
    """La capa que decide QUÉ SIGNIFICA una entrega en la vista del profesor.

    Existe por un bug real de la primera corrida (2026-08-07): `mod_assign_get_grades`
    devuelve una fila con grade `-1.00000` para las entregas que TODAVÍA no se corrigieron.
    Tomar "hay registro de nota" como "está corregida" mandó las 22 pendientes del curso a
    otro balde y el tablero mostró CERO pendientes en las 16 comisiones — le decía al
    profesor que estaba todo al día cuando no lo estaba.
    """

    @staticmethod
    def _subs(*filas):
        """filas: (userid, status, gradingstatus, timemodified)."""
        return {"assignments": [{"submissions": [
            {"userid": u, "status": st, "gradingstatus": gs, "timemodified": tm}
            for u, st, gs, tm in filas]}]}

    @staticmethod
    def _notas(*filas):
        """filas: (userid, grade, timemodified)."""
        return {"assignments": [{"grades": [
            {"userid": u, "grade": g, "timemodified": tm} for u, g, tm in filas]}]}

    def test_el_menos_uno_con_notgraded_es_PENDIENTE_no_corregido(self):
        # EL bug. Hay registro de nota, pero vale -1 y la entrega dice `notgraded`.
        r = panorama.clasificar_entregas(
            self._subs((7, "submitted", "notgraded", 1000)),
            self._notas((7, "-1.00000", 1000)))
        self.assertIn(7, r["pendientes"])
        self.assertNotIn(7, r["calificadas"])
        self.assertNotIn(7, r["sin_nota"])

    def test_los_new_no_son_entregas(self):
        # 21 de los 46 registros de la unidad 1 estaban en `new`: el alumno abrió la tarea
        # y nunca entregó. Contarlos casi duplicaba el trabajo reportado.
        r = panorama.clasificar_entregas(
            self._subs((1, "submitted", "notgraded", 1000), (2, "new", "notgraded", 1000)),
            self._notas())
        self.assertEqual(list(r["entregas"]), [1])

    def test_graded_con_nota_real_es_corregida(self):
        r = panorama.clasificar_entregas(
            self._subs((3, "submitted", "graded", 1000)),
            self._notas((3, "1.00000", 87400)))
        self.assertEqual(r["calificadas"][3], 87400)
        self.assertFalse(r["pendientes"])

    def test_graded_con_calificacion_vacia_es_hallazgo_no_corregida(self):
        # Moodle la saca de la cola, pero el alumno quedó sin nota y nadie lo espera.
        r = panorama.clasificar_entregas(
            self._subs((4, "submitted", "graded", 1000)),
            self._notas((4, "-1.00000", 2000)))
        self.assertIn(4, r["sin_nota"])
        self.assertNotIn(4, r["calificadas"])
        self.assertNotIn(4, r["pendientes"])

    def test_released_cuenta_como_corregida(self):
        r = panorama.clasificar_entregas(
            self._subs((5, "submitted", "released", 1000)),
            self._notas((5, "2.00000", 3000)))
        self.assertIn(5, r["calificadas"])

    def test_estado_intermedio_del_flujo_NO_cuenta_como_corregida(self):
        # La nota existe pero el alumno todavía no la tiene: el trabajo no está cerrado.
        r = panorama.clasificar_entregas(
            self._subs((6, "submitted", "readyforreview", 1000)),
            self._notas((6, "1.00000", 3000)))
        self.assertIn(6, r["pendientes"])
        self.assertNotIn(6, r["calificadas"])

    def test_sin_gradingstatus_no_se_asume_ninguno_de_los_dos(self):
        # "No sé" no se disfraza ni de corregido ni de pendiente: se cuenta aparte.
        r = panorama.clasificar_entregas(
            self._subs((8, "submitted", None, 1000)), self._notas())
        self.assertIn(8, r["sin_clasificar"])
        self.assertFalse(r["pendientes"])
        self.assertFalse(r["calificadas"])

    def test_vale_el_ultimo_intento(self):
        r = panorama.clasificar_entregas(
            self._subs((9, "submitted", "graded", 1000)),
            self._notas((9, "1.00000", 5000), (9, "2.00000", 9000)))
        self.assertEqual(r["calificadas"][9], 9000)

    def test_los_libros_cierran(self):
        # Réplica de la forma real de la unidad 1 de Prog I: 25 entregadas (15 corregidas /
        # 10 pendientes) + 21 `new`. La suma de los baldes tiene que dar las entregas.
        filas_sub, filas_nota = [], []
        for i in range(15):
            filas_sub.append((i, "submitted", "graded", 1000))
            filas_nota.append((i, "1.00000", 2000))
        for i in range(15, 25):
            filas_sub.append((i, "submitted", "notgraded", 1000))
            filas_nota.append((i, "-1.00000", 1000))
        for i in range(25, 46):
            filas_sub.append((i, "new", "notgraded", 1000))
        r = panorama.clasificar_entregas(self._subs(*filas_sub), self._notas(*filas_nota))
        self.assertEqual(len(r["entregas"]), 25)
        self.assertEqual(len(r["calificadas"]), 15)
        self.assertEqual(len(r["pendientes"]), 10)
        self.assertEqual(
            len(r["calificadas"]) + len(r["pendientes"]) + len(r["sin_nota"])
            + len(r["sin_clasificar"]), len(r["entregas"]))


class TestPanoramaEtiquetasYDias(unittest.TestCase):
    def test_reconoce_la_comision_y_descarta_lo_que_no_lo_es(self):
        self.assertEqual(panorama._etiqueta_comision("A26 C1-06"), "com6")
        self.assertEqual(panorama._etiqueta_comision("M26 C2-01"), "com1")
        self.assertEqual(panorama._etiqueta_comision("A25 C3-14"), "com14")
        # Grupos regionales y auxiliares NO son comisiones de tutoría.
        self.assertIsNone(panorama._etiqueta_comision("R-Rosario"))
        self.assertIsNone(panorama._etiqueta_comision("Grupo_2"))
        self.assertIsNone(panorama._etiqueta_comision("Entrego_1er_examen"))
        self.assertIsNone(panorama._etiqueta_comision(""))

    def test_sin_una_de_las_dos_fechas_devuelve_None_y_no_cero(self):
        # Un 0 acá se leería "se corrigió el mismo día". Es "no sé cuándo".
        self.assertIsNone(panorama._dias(0, 90000))
        self.assertIsNone(panorama._dias(90000, 0))
        # Corrección anterior a la entrega: dato incoherente, tampoco se inventa.
        self.assertIsNone(panorama._dias(90000, 1000))
        self.assertEqual(panorama._dias(0 + 86400, 86400 * 3), 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
