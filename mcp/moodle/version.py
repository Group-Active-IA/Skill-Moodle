"""Versión de la skill y aviso de actualización.

Por qué existe: la skill se instala con un `git clone` en la máquina de cada tutor y ahí
se queda. Sin un chequeo, alguien que la instaló hace dos meses sigue con los bugs de
hace dos meses y no tiene forma de enterarse — ni siquiera de que existe una versión
nueva. Esto compara el `VERSION` local contra el del repo y lo dice.

Decisión de diseño: **no poder chequear NO es un error del tutor y no debe molestarlo**.
Si no hay red, o GitHub no responde, se devuelve `disponible: None` y se sigue. Pero eso
viaja en la respuesta (nunca se finge "estás al día"): "no pude averiguarlo" y "estás al
día" son cosas distintas y se reportan distinto.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("skill.version")

# raw.githubusercontent en vez de `git fetch`: es un GET de 6 bytes y no toca el repo
# local del tutor (que puede tener cambios sin commitear).
_URL_REMOTA = "https://raw.githubusercontent.com/Group-Active-IA/Skill-Moodle/main/VERSION"
_TIMEOUT_S = 6.0
_CACHE_TTL_S = 86400  # 24 h: preguntar en cada llamada sería spam a GitHub


def _raiz() -> Path:
    """Raíz del repo de la skill (…/tup-campus-navigator), desde este archivo."""
    return Path(__file__).resolve().parent.parent.parent


def version_local() -> str:
    try:
        return (_raiz() / "VERSION").read_text().strip() or "desconocida"
    except OSError:
        return "desconocida"


def _tupla(v: str) -> tuple[int, ...]:
    """'1.10.2' -> (1, 10, 2). Sin esto, comparar como texto diría que 1.9 > 1.10."""
    partes = []
    for p in str(v).strip().split("."):
        try:
            partes.append(int(p))
        except ValueError:
            partes.append(0)
    return tuple(partes) or (0,)


def hay_novedad(local: str, remota: str) -> bool:
    if not remota or remota == "desconocida" or local == "desconocida":
        return False
    return _tupla(remota) > _tupla(local)


def _cache_path() -> Path:
    from . import almacen

    return Path(almacen.HOME) / ".version_check.json"


def _leer_cache() -> dict | None:
    try:
        d = json.loads(_cache_path().read_text())
        if time.time() - float(d.get("ts", 0)) < _CACHE_TTL_S:
            return d
    except (OSError, ValueError, TypeError):
        pass
    return None


def _escribir_cache(remota: str) -> None:
    try:
        _cache_path().write_text(json.dumps({"ts": time.time(), "remota": remota}))
    except OSError as e:
        log.warning("No pude cachear el chequeo de versión: %s", e)


async def _remota_en_vivo() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as http:
            r = await http.get(_URL_REMOTA)
            if r.status_code != 200:
                return None
            txt = r.text.strip()
            return txt if txt and len(txt) < 32 else None
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None


async def chequear(forzar: bool = False) -> dict:
    """Compara la versión instalada con la publicada.

    `disponible` es tri-estado a propósito: True (hay una nueva), False (estás al día),
    None (no se pudo averiguar). Colapsarlo en un bool haría que "no hay red" se leyera
    como "estás al día", que es exactamente la clase de mentira que esta skill viene
    sacándose de encima."""
    local = version_local()
    cache = None if forzar else _leer_cache()
    if cache:
        remota, de_cache = cache.get("remota"), True
    else:
        remota, de_cache = await _remota_en_vivo(), False
        if remota:
            _escribir_cache(remota)

    if not remota:
        return {
            "version_instalada": local,
            "version_publicada": None,
            "disponible": None,
            "aviso": "No pude consultar si hay una versión nueva (sin red o GitHub no "
                     "respondió). No es que estés al día: no lo sé.",
        }

    nueva = hay_novedad(local, remota)
    salida = {
        "version_instalada": local,
        "version_publicada": remota,
        "disponible": nueva,
        "_meta": {
            "fuente": "cache" if de_cache else "vivo",
            "ttl_s": _CACHE_TTL_S,
            # raw.githubusercontent responde con `cache-control: max-age=300` y no respeta
            # ni `Cache-Control: no-cache` ni un cache-buster en la query (verificado): la
            # versión recién publicada puede tardar unos minutos en verse. Para avisar de
            # actualizaciones da igual, pero se dice — un `disponible: false` recién
            # después de un push puede estar mirando el dato viejo.
            "latencia_cdn_s": 300,
        },
    }
    if not nueva and _tupla(local) > _tupla(remota):
        # El local es MÁS nuevo que lo publicado: pasa mientras el CDN se pone al día
        # después de un push, o si estás trabajando sobre la skill sin publicar.
        salida["nota"] = (
            f"Tenés la {local} y GitHub publica la {remota}: o venís de publicar hace un "
            "rato (el CDN tarda hasta 5 min) o estás sobre una versión sin publicar."
        )
    if nueva:
        salida["aviso"] = (
            f"Hay una versión nueva de la skill: {remota} (tenés la {local}). "
            "Actualizá con la tool `actualizar_skill` y reiniciá Claude Code."
        )
    return salida


async def _git(*args: str) -> tuple[int, str, str]:
    """Corre git en la raíz de la skill. (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(_raiz()), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


def _deps_del_panel_que_faltan() -> list[str]:
    """
    Dependencias del panel que no están instaladas.

    Devuelve `[]` cuando el panel no existe en esta instalación (nada que avisar)
    y cuando están todas. La lista se lee del propio `panel/requirements.txt`, no
    se hardcodea: un requirements que crece sin que nadie toque esta función
    volvería a dejar al tutor con una tool que no arranca.
    """
    import importlib.util

    reqs = _raiz() / "panel" / "requirements.txt"
    if not reqs.is_file():
        return []

    # `uvicorn[standard]` -> módulo `uvicorn`; `claude-agent-sdk` -> `claude_agent_sdk`.
    faltan = []
    for linea in reqs.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        paquete = linea.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
        if not paquete:
            continue
        if importlib.util.find_spec(paquete.replace("-", "_")) is None:
            faltan.append(paquete)
    return faltan


async def actualizar() -> dict:
    """Baja la última versión publicada con `git pull --ff-only`.

    `--ff-only` a propósito: si el repo local divergió, preferimos fallar y avisar antes
    que armar un merge automático en la máquina de alguien. Y si el tutor tiene cambios
    sin commitear, NO se toca nada: un `pull` que pisa trabajo ajeno es peor que no
    actualizar."""
    antes = version_local()

    rc, _, _ = await _git("rev-parse", "--git-dir")
    if rc != 0:
        return {"ok": False,
                "error": f"{_raiz()} no es un repo git, así que no puedo actualizar.",
                "siguiente_paso": "Reinstalá con install.sh desde el repo."}

    rc, sucio, _ = await _git("status", "--porcelain")
    if rc == 0 and sucio:
        # El nombre NO se corta por posición fija (l[3:]): `_git` hace .strip() sobre la
        # salida y eso come el espacio inicial de la primera línea de --porcelain, con lo
        # que "README.md" salía como "EADME.md". Partir por el primer bloque de espacios
        # funciona con o sin ese espacio, y maxsplit=1 preserva nombres con espacios.
        archivos = [l.split(maxsplit=1)[-1] for l in sucio.splitlines() if l.split()]
        return {
            "ok": False,
            "error": "Tenés cambios sin commitear en la skill: no toco nada para no pisarlos.",
            "archivos_modificados": archivos[:20],
            "siguiente_paso": "Guardá o descartá esos cambios y volvé a intentar.",
        }

    # HEAD ANTES del pull. No se usa el reflog (`HEAD@{1}`) para esto: el reflog se mueve
    # con cualquier operación —un commit del propio tutor, un checkout—, así que después
    # de commitear algo el "diff del pull" mostraba los archivos de ESE commit y la tool
    # decía "actualizado" y pedía reiniciar cuando el pull no había traído nada.
    _, head_antes, _ = await _git("rev-parse", "HEAD")

    rc, out, err = await _git("pull", "--ff-only")
    if rc != 0:
        return {"ok": False,
                "error": f"El `git pull` falló: {err or out}",
                "siguiente_paso": "Puede que el repo local haya divergido. Revisalo a mano."}

    despues = version_local()
    _, head_despues, _ = await _git("rev-parse", "HEAD")
    # "Hubo cambios" = el pull movió HEAD. Un pull puede traer commits sin subir el
    # VERSION, así que comparar sólo el número de versión no alcanza.
    bajo_algo = bool(head_antes) and bool(head_despues) and head_antes != head_despues

    cambiados = ""
    if bajo_algo:
        _, cambiados, _ = await _git("diff", "--name-only", head_antes, head_despues)
    toca_deps = "mcp/requirements.txt" in (cambiados or "")

    salida = {
        "ok": True,
        "version_anterior": antes,
        "version_actual": despues,
        "actualizado": bajo_algo,
        "detalle_git": out.splitlines()[-1] if out else "",
        # Sólo pedir reinicio si realmente cambió algo: decirle "reiniciá" a alguien que
        # ya estaba al día es ruido, y el ruido hace que después ignoren el aviso cuando sí
        # importa.
        "reiniciar": bajo_algo,
    }
    if bajo_algo:
        salida["siguiente_paso"] = (
            "IMPORTANTE: reiniciá Claude Code. El MCP se carga al arrancar la sesión, así "
            "que hasta que no reinicies seguís usando la versión vieja."
        )
        if toca_deps:
            salida["dependencias"] = (
                "requirements.txt cambió: corré "
                f"`{_raiz()}/.venv/bin/pip install -r {_raiz()}/mcp/requirements.txt` "
                "antes de reiniciar."
            )

        # El panel es opcional y trae dependencias propias. Para quien actualiza
        # por primera vez ese requirements no "cambió": apareció, así que el
        # `diff` de arriba no lo agarra. Y sin ellas `abrir_panel` está en el
        # menú y no arranca — una capacidad visible que no funciona es peor que
        # una que no está.
        panel_faltantes = _deps_del_panel_que_faltan()
        if panel_faltantes:
            salida["panel"] = (
                "La skill ahora trae un PANEL web (tool `abrir_panel`: chat + estado de "
                f"tus comisiones en el navegador). Le faltan {', '.join(panel_faltantes)}. "
                f"Para habilitarlo: `{_raiz()}/.venv/bin/pip install -r "
                f"{_raiz()}/panel/requirements.txt`. "
                "Es opcional: sin esto el resto de la skill funciona igual."
            )
    else:
        salida["aviso"] = ("Ya tenías la última versión: no había nada que bajar, "
                           "así que no hace falta reiniciar.")
    return salida
