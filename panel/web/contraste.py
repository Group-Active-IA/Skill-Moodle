#!/usr/bin/env python3
"""
Verifica por cálculo el contraste de la paleta del panel.

Existe porque el DESIGN.md del proyecto dice que el contraste se verifica y no se
estima, y porque este producto tiene documentado lo caro que sale dar por bueno un
número que nadie cruzó contra nada.

Correr:  python3 panel/web/contraste.py
Sale con código 1 si algún par no llega al mínimo que declara.
"""

from __future__ import annotations

import math
import sys

# --------------------------------------------------------------------------- #
# OKLCH -> sRGB
# --------------------------------------------------------------------------- #


def oklch_a_srgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    hr = math.radians(h)
    a, b = C * math.cos(hr), C * math.sin(hr)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l, m, s = l_**3, m_**3, s_**3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def gamma(x: float) -> float:
        x = max(0.0, min(1.0, x))
        return 1.055 * (x ** (1 / 2.4)) - 0.055 if x > 0.0031308 else 12.92 * x

    return gamma(r), gamma(g), gamma(bl)


def luminancia(rgb: tuple[float, float, float]) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = luminancia(a), luminancia(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hexa(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


# --------------------------------------------------------------------------- #
# La paleta
# --------------------------------------------------------------------------- #

CLARO = {
    "papel": (0.988, 0.004, 85),
    "papel-nota": (0.967, 0.006, 80),
    "papel-code": (0.955, 0.008, 255),
    "tinta": (0.24, 0.016, 255),
    "tinta-media": (0.46, 0.014, 255),
    "tinta-tenue": (0.63, 0.011, 255),
    "correccion": (0.505, 0.196, 27),
    "correccion-suave": (0.955, 0.022, 27),
    "verificado": (0.52, 0.12, 150),
    "atencion": (0.56, 0.13, 75),
}

OSCURO = {
    "papel": (0.185, 0.008, 255),
    "papel-nota": (0.225, 0.009, 255),
    "papel-code": (0.255, 0.010, 255),
    "tinta": (0.945, 0.005, 85),
    "tinta-media": (0.76, 0.010, 255),
    "tinta-tenue": (0.62, 0.010, 255),
    "correccion": (0.72, 0.150, 27),
    "correccion-suave": (0.30, 0.060, 27),
    "verificado": (0.78, 0.130, 150),
    "atencion": (0.82, 0.130, 75),
}

# (texto, fondo, mínimo exigido, para qué)
PARES = [
    ("tinta", "papel", 4.5, "cuerpo del hilo"),
    ("tinta", "papel-nota", 4.5, "texto en sidebar e input"),
    ("tinta", "papel-code", 4.5, "código y salidas"),
    ("tinta-media", "papel", 4.5, "metadatos y procedencia"),
    ("tinta-media", "papel-nota", 4.5, "procedencia en sidebar"),
    ("tinta-tenue", "papel", 3.0, "timestamps (texto grande / no esencial)"),
    ("correccion", "papel", 4.5, "acento sobre papel"),
    ("correccion", "correccion-suave", 4.5, "texto del bloque de confirmación"),
    ("verificado", "papel", 4.5, "estado verificado"),
    ("atencion", "papel", 4.5, "estado de atención"),
]


# --------------------------------------------------------------------------- #
# La escala del mapa de entregas (secuencial, un solo hue)
#
# Magnitud pide un hue y luz creciente, nunca un arcoíris. Acá el hue es la
# tinta: más entregas, más tinta sobre el papel. El rojo NO participa — está
# reservado para lo irreversible, y una celda con muchas entregas no es un
# peligro.
#
# La intensidad es refuerzo, no información: el número va escrito en cada celda.
# Por eso lo que hay que verificar es que ese número se lea sobre su propio
# fondo, en los dos temas.
# --------------------------------------------------------------------------- #

ESCALA_CLARO = [
    ("nivel-0", (0.975, 0.004, 255)),
    ("nivel-1", (0.925, 0.018, 255)),
    ("nivel-2", (0.855, 0.032, 255)),
    ("nivel-3", (0.740, 0.048, 255)),
    # El nivel-4 estaba en L 0.585 y daba 4.05:1 contra las DOS tintas: la banda
    # muerta del medio de toda escala secuencial, donde ni el texto claro ni el
    # oscuro llegan. Se baja hasta que el papel despega. Lo encontró el script,
    # no el ojo.
    ("nivel-4", (0.535, 0.066, 255)),
    ("nivel-5", (0.400, 0.070, 255)),
]

ESCALA_OSCURO = [
    ("nivel-0", (0.225, 0.008, 255)),
    ("nivel-1", (0.290, 0.022, 255)),
    ("nivel-2", (0.375, 0.038, 255)),
    ("nivel-3", (0.485, 0.055, 255)),
    ("nivel-4", (0.625, 0.070, 255)),
    ("nivel-5", (0.775, 0.075, 255)),
]


def revisar_escala(nombre: str, escala: list, tinta: tuple, papel: tuple) -> list[str]:
    """
    El número de cada celda se pinta con tinta o con papel según qué contraste
    mejor contra el fondo de esa celda. Se verifica que el mejor de los dos
    llegue a 4.5:1 en TODOS los niveles: si un solo nivel no llega, ese número
    no se lee y la celda deja de informar.
    """
    fallas: list[str] = []
    print(f"\n  ESCALA DEL MAPA · {nombre}")
    print("  " + "-" * 74)
    for etiqueta, oklch in escala:
        fondo = oklch_a_srgb(*oklch)
        c_tinta = contraste(oklch_a_srgb(*tinta), fondo)
        c_papel = contraste(oklch_a_srgb(*papel), fondo)
        mejor, cual = (c_tinta, "tinta") if c_tinta >= c_papel else (c_papel, "papel")
        ok = mejor >= 4.5
        print(
            f"  {'ok  ' if ok else 'FALLA'} {mejor:5.2f}:1  {etiqueta} "
            f"({hexa(fondo)})  el número va en {cual}"
        )
        if not ok:
            fallas.append(f"{nombre}: el número sobre {etiqueta} da {mejor:.2f}:1")
    return fallas


def revisar(nombre: str, paleta: dict) -> list[str]:
    fallas: list[str] = []
    print(f"\n  {nombre}")
    print("  " + "-" * 74)
    for texto, fondo, minimo, para in PARES:
        c = contraste(oklch_a_srgb(*paleta[texto]), oklch_a_srgb(*paleta[fondo]))
        ok = c >= minimo
        marca = "ok  " if ok else "FALLA"
        print(f"  {marca} {c:5.2f}:1  (min {minimo})  {texto} sobre {fondo:17} {para}")
        if not ok:
            fallas.append(f"{nombre}: {texto} sobre {fondo} da {c:.2f}:1, exige {minimo}")
    return fallas


def main() -> int:
    print("\nContraste de la paleta del panel (WCAG 2.1, calculado)")
    fallas = revisar("CLARO", CLARO) + revisar("OSCURO", OSCURO)
    fallas += revisar_escala(
        "CLARO", ESCALA_CLARO, CLARO["tinta"], CLARO["papel"]
    ) + revisar_escala("OSCURO", ESCALA_OSCURO, OSCURO["tinta"], OSCURO["papel"])

    print("\n  Valores sRGB (para pegar donde haga falta)")
    print("  " + "-" * 74)
    for nombre, paleta in (("claro", CLARO), ("oscuro", OSCURO)):
        muestras = "  ".join(f"{k}={hexa(oklch_a_srgb(*v))}" for k, v in list(paleta.items())[:4])
        print(f"  {nombre:7} {muestras}")

    if fallas:
        print("\n  NO PASA:")
        for f in fallas:
            print(f"   - {f}")
        return 1

    print(f"\n  Pasan los {len(PARES) * 2} pares.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
