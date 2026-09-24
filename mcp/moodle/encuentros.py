"""El MATERIAL de un encuentro — lo que el agente necesita para contestarle a un alumno.

Por qué existe este módulo: en la modalidad de Encuentros de Prog I el alumno mira
cápsulas de video a su ritmo, repasa con un apunte interactivo y pregunta en un foro
durante la ventana horaria. Cuando llega la duda —«no entendí lo del video de
diccionarios»— el agente tiene que contestar **con lo que explicó el docente**, no con
Python genérico. Sin este módulo no tiene de dónde sacarlo, y responder de memoria en un
foro que ven las 27 comisiones, firmado por el tutor, es exactamente la clase de error
que esta skill persigue: plausible, convincente y equivocado.

El apunte interactivo resuelve el problema entero. Está escrito video por video
(«VIDEO 03 · Sets»), con los mismos ejemplos y los mismos gotchas que el docente dio en
cámara. Traerlo es todo lo que hace falta: la playlist no se toca.

La URL del apunte NO se hardcodea. Cambia en cada encuentro (una unidad, un apunte) y
sale del propio campus, del label de la sección de Encuentros — la misma regla que ordena
el resto de la skill: **verificar en vivo, nunca inventar**. Si no se puede determinar
cuál es, este módulo dice «no pude» y devuelve los candidatos; jamás elige a ciegas ni
cae al apunte de la unidad pasada, que es el modo de falla peligroso (contesta con total
confianza sobre el tema equivocado).
"""

import html as _html
import re
from urllib.parse import urlparse

import httpx

_TIMEOUT_S = 30.0
_MAX_BYTES = 2_000_000

# Hosts que NUNCA son el apunte. El label de la sección mezcla tres cosas: el video, la
# reunión y el material — y sólo la tercera se puede leer como texto.
_HOSTS_VIDEO = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
                "vimeo.com", "www.vimeo.com"}
_HOSTS_REUNION = {"meet.google.com", "teams.microsoft.com", "teams.live.com",
                  "www.teams.live.com", "zoom.us"}

_RE_SCRIPT = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1>", re.I | re.S)
_RE_BLOQUE = re.compile(r"</?(p|div|br|li|tr|h[1-6]|section|article|header|footer|pre)\b[^>]*>", re.I)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_TITULO = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RE_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _es_reunion(host: str) -> bool:
    """`utn.zoom.us` y `zoom.us` son el mismo servicio: se compara por sufijo."""
    return any(host == h or host.endswith("." + h) for h in _HOSTS_REUNION)


def elegir_apunte(links: list[dict], base_url: str) -> dict:
    """De los links de la sección, cuál es el apunte. PURA: no pide nada por red.

    Cada elemento de `links` es `{"url": str, "cmid": int}`. Es apunte el link http(s)
    que no apunta al propio campus, ni a un video, ni a una sala de reunión.

    Devuelve `elegido` (o None), `candidatos` y `descartados` con el motivo de cada uno.
    Cuando hay más de un candidato NO se calla: elige el del `cmid` más alto —el módulo
    agregado más tarde, que es el del encuentro vigente— y lo deja dicho en `motivo`,
    para que el agente pueda avisarle al tutor en vez de descubrirlo tarde."""
    campus = _host(base_url)
    candidatos: list[dict] = []
    descartados: list[dict] = []
    vistos: set[str] = set()

    for item in links:
        url = (item.get("url") or "").strip()
        if not url or url in vistos:
            continue
        vistos.add(url)
        h = _host(url)
        if not url.lower().startswith(("http://", "https://")):
            descartados.append({"url": url, "motivo": "no es http(s)"})
        elif campus and (h == campus or h.endswith("." + campus)):
            descartados.append({"url": url, "motivo": "es del propio campus"})
        elif h in _HOSTS_VIDEO:
            descartados.append({"url": url, "motivo": "es un video"})
        elif _es_reunion(h):
            descartados.append({"url": url, "motivo": "es una sala de reunión"})
        else:
            candidatos.append({"url": url, "cmid": item.get("cmid")})

    if not candidatos:
        return {"elegido": None, "candidatos": [], "descartados": descartados,
                "motivo": "Ningún link de la sección parece un apunte: todos son del "
                          "campus, videos o salas de reunión."}

    candidatos.sort(key=lambda c: (c.get("cmid") or 0), reverse=True)
    elegido = candidatos[0]
    if len(candidatos) == 1:
        motivo = "Único link de la sección que no es campus, video ni reunión."
    else:
        motivo = (f"Hay {len(candidatos)} candidatos. Se tomó el del cmid más alto "
                  f"({elegido['cmid']}), que es el módulo agregado más tarde. "
                  f"Si el encuentro de hoy no es el de ese apunte, pasá la `url` a mano.")
    return {"elegido": elegido, "candidatos": candidatos, "descartados": descartados,
            "motivo": motivo}


def html_a_texto(html: str) -> str:
    """HTML del apunte a texto plano legible, conservando los saltos de bloque.

    Saca `<script>`/`<style>` ENTEROS antes de tocar los tags: los apuntes se publican
    con frameworks que embeben el estado de la página como JSON al final del documento,
    y un strip de tags a secas lo deja adentro — kilobytes de ruido que el agente lee
    como si fuera contenido."""
    t = _RE_SCRIPT.sub(" ", html or "")
    t = _RE_BLOQUE.sub("\n", t)
    t = _RE_TAG.sub(" ", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def titulo_de(html: str) -> str:
    """El `<title>` del apunte. Es lo que dice de qué unidad es, y sirve para que el
    tutor detecte de un vistazo que el link del campus quedó en la unidad pasada."""
    m = _RE_TITULO.search(html or "")
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip() if m else ""


def links_de_seccion(seccion: dict) -> list[dict]:
    """Todos los links de una sección: los del summary y los de cada módulo, con el cmid
    del módulo donde aparecieron.

    Incluye a propósito los módulos con `visible=0`. El label de la modalidad nueva y el
    foro del encuentro están OCULTOS a los estudiantes hasta la hora del encuentro
    (verificado en vivo, curso 74, 2026-09-23): filtrar por visibilidad los dejaría
    afuera justo cuando hay que prepararlos."""
    out: list[dict] = []
    for url in _RE_HREF.findall(seccion.get("summary", "") or ""):
        out.append({"url": url.strip(), "cmid": None})
    for mod in seccion.get("modules", []) or []:
        cmid = mod.get("id")
        if mod.get("url"):
            out.append({"url": mod["url"], "cmid": cmid})
        for url in _RE_HREF.findall(mod.get("description", "") or ""):
            out.append({"url": url.strip(), "cmid": cmid})
    return out


def secciones_de_encuentros(secciones: list) -> list[dict]:
    """Las secciones del curso que son de encuentros. PURA.

    Matchea por el texto del nombre sin acentos, porque el campus escribe «Encuentros
    síncronos» en la sección y «Encuentros Sincronicos» en el label de adentro."""
    out = []
    for sec in secciones or []:
        nombre = _RE_TAG.sub(" ", str(sec.get("name") or ""))
        plano = _html.unescape(nombre).lower()
        plano = (plano.replace("ó", "o").replace("í", "i").replace("é", "e")
                      .replace("á", "a").replace("ú", "u"))
        if "encuentro" in plano:
            out.append(sec)
    return out


async def bajar_apunte(url: str) -> dict:
    """Descarga el apunte y lo devuelve como texto. Devuelve `ok: False` con el motivo
    ante cualquier falla: un apunte que no se pudo leer NO es un apunte vacío."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as http:
            r = await http.get(url)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"No pude descargar el apunte ({url}): {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"El apunte respondió HTTP {r.status_code} ({url})."}
    if len(r.content) > _MAX_BYTES:
        return {"ok": False, "error": f"El apunte pesa {len(r.content)} bytes, más del tope."}

    crudo = r.text
    texto = html_a_texto(crudo)
    if len(texto) < 200:
        return {"ok": False, "error": (
            f"Bajé {url} pero salieron sólo {len(texto)} caracteres de texto. "
            "Probablemente el contenido se arme en el navegador y no venga en el HTML: "
            "no lo uses como material, no tengo con qué contestar.")}
    return {"ok": True, "url": url, "titulo": titulo_de(crudo), "texto": texto,
            "caracteres": len(texto)}


async def material_encuentro(client, base_url: str, course_id: int,
                             url: str | None = None) -> dict:
    """El material del encuentro vigente, listo para contestarle a un alumno.

    Con `url` baja ese apunte y no toca el campus. Sin `url` lo descubre: lee las
    secciones del curso, se queda con las de encuentros y elige el apunte entre sus
    links."""
    if url:
        bajado = await bajar_apunte(url)
        if not bajado.get("ok"):
            return {"ok": False, "course_id": course_id, "error": bajado["error"]}
        return {"ok": True, "course_id": course_id, "apunte": bajado,
                "origen": {"fuente": "url pasada a mano", "cmid": None, "seccion": None},
                "candidatos": [], "descartados": []}

    try:
        secciones = await client.ws("core_course_get_contents", {"courseid": course_id})
    except Exception as e:  # MoodleWSError y cualquier fallo de red
        return {"ok": False, "course_id": course_id,
                "error": f"No pude leer el contenido del curso {course_id}: {e}"}
    if not isinstance(secciones, list):
        return {"ok": False, "course_id": course_id,
                "error": f"Respuesta inesperada de core_course_get_contents (curso {course_id})."}

    de_encuentros = secciones_de_encuentros(secciones)
    if not de_encuentros:
        return {"ok": False, "course_id": course_id,
                "error": ("Este curso no tiene ninguna sección de encuentros. "
                          "Si el material vive en otro lado, pasá la `url` del apunte.")}

    links: list[dict] = []
    nombres = []
    for sec in de_encuentros:
        nombres.append(_html.unescape(_RE_TAG.sub(" ", str(sec.get("name") or ""))).strip())
        links.extend(links_de_seccion(sec))

    eleccion = elegir_apunte(links, base_url)
    if not eleccion["elegido"]:
        return {"ok": False, "course_id": course_id, "secciones": nombres,
                "error": eleccion["motivo"], "descartados": eleccion["descartados"]}

    bajado = await bajar_apunte(eleccion["elegido"]["url"])
    if not bajado.get("ok"):
        return {"ok": False, "course_id": course_id, "secciones": nombres,
                "error": bajado["error"], "candidatos": eleccion["candidatos"]}

    return {
        "ok": True,
        "course_id": course_id,
        "apunte": bajado,
        "origen": {"fuente": "descubierto en el campus", "secciones": nombres,
                   "cmid": eleccion["elegido"]["cmid"], "motivo": eleccion["motivo"]},
        "candidatos": eleccion["candidatos"],
        "descartados": eleccion["descartados"],
    }
