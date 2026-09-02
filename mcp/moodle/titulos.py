"""Qué dice el título de una actividad. UN solo lugar que lo sepa.

Hasta acá esto estaba resuelto tres veces, cada una con su regex y su criterio:
`ws_api.es_actividad_de_cierre` (qué cuenta para la racha de abandono),
`panorama._nro_unidad` (la columna RETRASO del informe de coordinación) y
`panel.backend.dia._unidad` (las columnas de la grilla). Tres copias del mismo
conocimiento envejecen por separado: al mapear Matemática las TRES dieron cero
—ninguna reconoció una sola de sus 15 actividades— y cada una había que
encontrarla por su lado.

**El campus no expone la unidad como campo.** El título es lo único que hay, y
cada cátedra lo escribe a su manera. Hoy hay dos vocabularios, los dos
verificados en vivo el 2026-09-02:

  Programación   "Actividad de cierre unidad 4 - Git 🎯🏁"      -> unidad 4
  Matemática     "ENTREGA U3S1: Ejercicio 3, 4 y 12 de la ..."  -> unidad 3, semana 1
  Calificador    "Video 2 Semana 1 SN"                          -> semana 1, sin unidad

El de Matemática trae un eje que Programación no tiene: la SEMANA. Una unidad
puede tener dos o tres entregas (U5 tiene tres) y no son la misma actividad.

El tercero es el del LIBRO DE CALIFICACIONES de Matemática, y es el que obliga a
que `semana` pueda venir sola. Los videos, lecciones y autoevaluaciones dicen la
semana ("Video 2 Semana 1 SN") pero NO la unidad: la unidad la nombra la sección
del curso que los contiene ("2- Sistema binario"), no el título. Devolver
`semana=None` ahí sería tirar el único eje que el título sí declara — y ordenar
los 8 videos de Lógica alfabéticamente pone el 4 de la semana 1 después del 1 de
la semana 2. Quién resuelve la unidad por sección es `calificador.py`; acá se lee
lo que el título dice y nada más.

Regla de precedencia, y es la que arregla el bug: **el código estructurado
`U{n}S{m}` gana sobre las palabras sueltas.** Sin eso, "ENTREGA U2S1: Ejercicio 8
de la Práctica 1" caía en la búsqueda por palabra, encontraba "práctica" y
devolvía la unidad 1 — el número del CUADERNILLO, no el de la unidad. Daba mal
13 de las 15 de Matemática, y en la grilla se leía como cuatro actividades de la
unidad 1: un dato falso, no un blanco sospechoso.

Agregar una materia nueva es agregar un patrón acá y nada más.
"""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

# `U4S2` · `u4 s2` · `U10S1`. Anclado al arranque de palabra para no comerse un
# "u1" que viva dentro de otra cosa.
_RE_UNIDAD_SEMANA = re.compile(r"\bu\s*(\d{1,2})\s*s\s*(\d{1,2})\b", re.I)
# El vocabulario de Programación: la palabra "unidad" seguida del número.
_RE_UNIDAD_PALABRA = re.compile(r"unidad\s*(\d+)", re.I)
# "Actividad de cierre ..." — la cadencia semanal de Programación.
_RE_CIERRE_PALABRA = re.compile(r"actividad\s+de\s+cierre|cierre", re.I)
# La SEMANA sola, sin unidad: "Video 2 Semana 1 SN", "Sistema Binario Lección semana 2".
# Se prueba ÚLTIMA y sólo cuando no hubo código `U{n}S{m}`: ahí la semana ya vino.
_RE_SEMANA_PALABRA = re.compile(r"semana\s*(\d{1,2})", re.I)

# Instancias que NO son la cadencia semanal: tienen calendario y dinámica propios.
# El Integrador es grupal y de una sola entrega al final; medido en vivo, 13 de 16
# alumnos de una comisión no lo habían entregado, así que contarlo en la racha
# marcaba en amarillo a gente que venía al día.
_FUERA_DE_CADENCIA = ("integrador", "parcial", "recuperatorio", "tio", "extraordinari")


def _norm(txt: str) -> str:
    """Minúsculas y sin acentos. Mismo criterio que `ws_api._norm`."""
    s = unicodedata.normalize("NFKD", str(txt or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


class Actividad(NamedTuple):
    """Lo que se pudo leer del título. PURA: no consulta el campus.

    `unidad`/`semana` en None = **no se pudo leer**, que no es lo mismo que cero.
    Quien llama tiene que poder distinguir "esta actividad no declara unidad" de
    "es la unidad 0", igual que `disponible` en `version_skill` o `es_escala` en
    `cargar_nota`: ante la duda no se inventa un número.
    """

    unidad: int | None
    semana: int | None
    fuera_de_cadencia: bool  # integrador / parcial / recuperatorio / extraordinaria

    @property
    def es_cadencia(self) -> bool:
        """¿Cuenta para la racha de abandono? Sólo si declara unidad y no es una
        instancia con calendario propio."""
        return self.unidad is not None and not self.fuera_de_cadencia

    @property
    def etiqueta(self) -> str | None:
        """Etiqueta corta de columna: `U4` o `U4S2`. None si no se pudo leer.

        Con semana el rótulo NO se puede acortar a `U4`: la U5 de Matemática tiene
        tres entregas y tres columnas que digan `U5` son tres columnas que el
        lector no puede distinguir.
        """
        if self.unidad is None:
            return None
        if self.semana is None:
            return f"U{self.unidad}"
        return f"U{self.unidad}S{self.semana}"


def leer(titulo: str) -> Actividad:
    """Lee el título de una actividad. PURA.

    El código `U{n}S{m}` se prueba PRIMERO: es explícito y sin ambigüedad, así que
    cuando está, gana. Recién si no está se sale a buscar por palabra, que es
    adivinar y por eso va último.
    """
    t = _norm(titulo)
    fuera = any(p in t for p in _FUERA_DE_CADENCIA)

    m = _RE_UNIDAD_SEMANA.search(t)
    if m:
        return Actividad(int(m.group(1)), int(m.group(2)), fuera)

    # La semana suelta es un dato de segunda: no dice qué unidad es, así que no
    # puede cambiar `unidad` ni `es_cadencia`. Sólo rellena el eje que el título
    # sí declara, para poder ORDENAR los videos de una unidad como se cursan.
    ms = _RE_SEMANA_PALABRA.search(t)
    semana = int(ms.group(1)) if ms else None

    m = _RE_UNIDAD_PALABRA.search(t)
    if m:
        return Actividad(int(m.group(1)), semana, fuera)

    return Actividad(None, semana, fuera)


def es_actividad_de_cierre(titulo: str) -> bool:
    """Si una tarea es de la cadencia semanal (cuenta para la racha) o no.

    Programación lo dice con "Actividad de cierre unidad N"; Matemática, con el
    código `U{n}S{m}` y sin la palabra "cierre" en ningún lado. Las dos son la
    cadencia de su materia.
    """
    act = leer(titulo)
    if act.fuera_de_cadencia:
        return False
    # Con unidad declarada alcanza: es la cadencia de la materia, la nombre como la
    # nombre. `_RE_CIERRE_PALABRA` queda para el aula que dice "cierre" sin numerar.
    return act.unidad is not None or bool(_RE_CIERRE_PALABRA.search(_norm(titulo)))


def nro_unidad(titulo: str) -> int | None:
    """Número de unidad, o None si el título no lo declara."""
    return leer(titulo).unidad


def etiqueta(titulo: str) -> str | None:
    """`U4` / `U4S2`, o None si no se pudo leer."""
    return leer(titulo).etiqueta


def orden(titulo: str) -> tuple[int, int, int, str]:
    """Orden de lectura: como cursa el alumno, no como lo numeró Moodle.

    Ordenar por `assign_id` parece neutral y no lo es: en Prog IV los parciales
    tienen id más bajo que la unidad 1, así que la grilla arrancaba por el parcial
    y ponía la U1 después de la U10.

    Primero las unidades (por unidad y después por SEMANA — sin la semana, las tres
    entregas de la U5 de Matemática quedaban en orden alfabético del título),
    después el integrador, después los parciales, y al final lo no clasificado,
    alfabético para que al menos sea estable entre corridas.
    """
    t = _norm(titulo)
    act = leer(titulo)

    if act.unidad is not None and not act.fuera_de_cadencia:
        return (0, act.unidad, act.semana or 0, titulo)
    if "integrador" in t:
        return (1, 0, 0, titulo)
    if "parcial" in t or "recuperatorio" in t:
        return (2, 0, 0, titulo)
    return (3, 0, 0, titulo)
