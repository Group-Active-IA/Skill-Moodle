"""Qué es un GRUPO del curso. UN solo lugar que lo sepa.

Un curso del campus TUP tiene muchos más grupos que comisiones: conviven las comisiones
de tutoría, las 17 regionales `R-*`, los grupos de horario ("Miercoles 19:00 Hs."), los
auxiliares ("Entrego_1er_examen", "Tutores Académicos") y los de instancias especiales
("Coloquio Final Integrador"). Contarlos juntos da números que parecen del padrón y no lo
son: en Prog II, 32 grupos de los que sólo 15 son comisiones, y cruzar los otros 17 contra
una tarea produjo un conteo de alumnos «invisibles» inflado al doble.

Ese criterio estaba escrito DOS veces —`panorama._RE_COMISION` y `ws_api._RE_COMISION`—
con dos regex parecidas y distintas, y las dos daban CERO en Probabilidad y Estadística.
Es exactamente la historia de `titulos.py`: el mismo conocimiento duplicado envejece por
separado y falla junto, en la materia nueva y sin hacer ruido.

**Lo que pasó, y por qué importa tanto.** PyE (course 79) nombra sus comisiones
`Comisión 1` … `Comisión 15`, no `A26 C1-01`. Las dos regex exigían el prefijo de cohorte,
así que las 15 caían en `grupos_ignorados` y el curso quedaba con **0 comisiones**. Sin
comisiones no hay padrón, y sin padrón `reporte_coordinacion`, `informes_nexos`,
`demora_correccion` e `informe_alumnos` no devuelven un error: devuelven un curso vacío.
La materia entera era invisible para la skill y ningún número decía por qué.

Dos vocabularios, los dos verificados en vivo el 2026-09-02:

    Prog I-IV / Matemática   "A26 C1-06"     -> com6   (15-16 comisiones)
    Prob. y Estadística      "Comisión 7"    -> com7   (15 comisiones)

En el primero la letra es la cohorte de INGRESO del alumno (Agosto/Marzo) y no el
cuatrimestre del aula — ese gotcha ya está documentado en `comisiones.json` y acá sólo hay
que no tropezarlo. El número que vale es **el último**: en "A26 C1-06" el `C1` es la
materia (Programación I) y el `06` la comisión.

Agregar un aula con otro vocabulario es agregar un patrón acá y nada más.
"""

from __future__ import annotations

import re

COMISION = "comision"
REGIONAL = "regional"
OTRO = "otro"

# "A26 C1-06" · "M26 C2-14" · "A25 C3-01". Anclado a los dos extremos: sin eso,
# "A26 C1-06 (baja)" pasaría como comisión y no lo es.
_RE_COHORTE = re.compile(r"^\s*[A-Z]\d{2}\s+C\d+\s*-\s*(\d+)\s*$", re.I)

# "Comisión 7" · "Comision 7" · "COMISIÓN N° 7" · "Comisión Nº 7". El acento y el ordinal
# son opcionales porque el campus los escribe de las dos formas.
# Anclado al final a propósito: "Comisión 1 - Coloquio" NO es la comisión 1, y
# "Inscripción a Comisiones" no matchea porque exige el número.
_RE_SIMPLE = re.compile(r"^\s*comisi[oó]n\s*(?:n\s*[°ºo]?\s*)?(\d{1,3})\s*$", re.I)

# Las regionales (sedes). Son las que `etiqueta` descarta como comisión y a la vez el dato
# que necesita el informe de nexos: para una mitad del código son ruido y para la otra son
# la respuesta.
# Case-insensitive, y no es cosmético: la copia de `ws_api` comparaba en mayúsculas y la
# de `panorama` no, así que "r-córdoba" era regional para una mitad de la skill y "otro"
# para la otra. Unificar sin esto perdía silenciosamente una sede — lo cazó un test que ya
# existía, que es justamente para lo que estaba escrito.
_RE_REGIONAL = re.compile(r"^\s*R\s*-\s*(.+?)\s*$", re.I)


def etiqueta(nombre: str) -> str | None:
    """`'A26 C1-06'` / `'Comisión 6'` -> `'com6'`. `None` si no es una comisión.

    PURA. Se devuelve la forma corta porque es la que usan el reparto interno,
    `mi_comision` y los tutores al hablar; el nombre completo del campus viaja igual en
    cada fila, así que no se pierde nada.
    """
    n = nombre or ""
    m = _RE_COHORTE.match(n) or _RE_SIMPLE.match(n)
    return f"com{int(m.group(1))}" if m else None


def es_comision(nombre: str) -> bool:
    return etiqueta(nombre) is not None


def regional_de(nombre: str) -> str | None:
    """`'R-Rosario'` -> `'Rosario'`. `None` si el grupo no es una regional.

    La sede del alumno sale de sus grupos y no de su perfil: el campo `city` viene en el
    71% de los alumnos, es texto libre y trae "Córdoba" y "Cordoba" como valores distintos.
    Sería una regional inventada en tres de cada diez filas.
    """
    m = _RE_REGIONAL.match(nombre or "")
    return m.group(1) if m else None


def clasificar(nombre: str) -> str:
    """`comision` · `regional` · `otro`. PURA.

    `otro` no es un cajón de descarte silencioso: quien llama devuelve esos nombres en
    `grupos_ignorados` para que se vea QUÉ se dejó afuera. Un grupo nuevo que la skill no
    entienda tiene que ser visible, no invisible.
    """
    if es_comision(nombre):
        return COMISION
    if regional_de(nombre) is not None:
        return REGIONAL
    return OTRO


def numero(etiqueta_com: str) -> int:
    """`'com6'` -> `6`, para ordenar. `0` si no se puede leer, que ordena primero y se ve."""
    try:
        return int(str(etiqueta_com)[3:])
    except (TypeError, ValueError):
        return 0
