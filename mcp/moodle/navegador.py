"""Pase con navegador (Playwright) — el paso 2 de la auditoría, para lo que la API REST
NO puede ver:

  1. **Cantidad de preguntas** de cada cuestionario (la planilla pide mini=4, autoeval=10;
     la API mobile no la expone sin iniciar un intento, pero el tutor tiene rol docente y
     puede abrir `mod/quiz/edit.php` y contar los slots).
  2. **Apps de Google** (NotebookLM/Colab): un GET siempre parece login; el navegador
     ejecuta el JS y ve el estado final → distingue "abre en abierto" de "pide cuenta".

Es OPCIONAL: si Playwright no está instalado, `pase_navegador` devuelve un aviso y la
auditoría sigue con lo que dio la API. La sesión se persiste (storageState) para no
re-loguear en cada corrida. Read-only: solo navega, nunca escribe en el campus.
"""

import os
import re

_AUTH_DIR = os.path.expanduser(os.environ.get("MOODLE_SKILL_HOME", "~/.moodle-skill"))
_STORAGE = os.path.join(_AUTH_DIR, ".auth", "moodle-state.json")
_RE_PREG_TXT = re.compile(r"(\d+)\s+preguntas?", re.I)
# Señales de ACCESO DENEGADO en el render final. OJO: NO se incluye "iniciar sesión /
# sign in / elegir cuenta" — Colab y otras apps muestran ese botón en su chrome aunque el
# recurso SEA público (un notebook público abría y se marcaba 'pide_cuenta' — falso
# positivo verificado). Solo cuentan las frases de permiso denegado.
_RE_LOGIN_WALL = re.compile(
    r"solicitar acceso|solicita acceso|pedir acceso|request access|necesitas acceso|"
    r"you need access|no ten[eé]s permiso|no tienes permiso|permission denied", re.I)


def disponible() -> bool:
    """¿Está Playwright instalado? (import perezoso: la skill no lo exige.)"""
    try:
        import playwright.async_api  # noqa: F401
        return True
    except ImportError:
        return False


async def _login(page, base: str, user: str, pw: str) -> bool:
    """Login por formulario. Detección robusta (no depende de un solo selector): quedó
    logueado si ya NO está en login/index.php Y aparece el menú de usuario."""
    await page.goto(f"{base}/login/index.php", wait_until="domcontentloaded")
    await page.fill("#username", user)
    await page.fill("#password", pw)
    await page.click("#loginbtn")
    await page.wait_for_load_state("domcontentloaded")
    sigue_en_login = "login/index.php" in page.url
    menu = await page.locator("#user-menu-toggle, .usermenu, a[href*='logout']").count()
    return not sigue_en_login and menu > 0


async def _contar_preguntas(page, base: str, cmid) -> int | None:
    """Preguntas de un quiz abriendo su página de edición y contando los slots. Devuelve
    None si no se pudo (sin permiso, cuestionario raro): "sin dato", no se inventa."""
    try:
        await page.goto(f"{base}/mod/quiz/edit.php?cmid={cmid}", wait_until="domcontentloaded")
        n = await page.locator("li.slot").count()
        if n:
            return n
        # Fallback por texto ("N preguntas") si el markup del tema cambió.
        m = _RE_PREG_TXT.search(await page.locator("body").inner_text())
        return int(m.group(1)) if m else None
    except Exception:  # noqa: BLE001 — un quiz que no abre no rompe el pase entero
        return None


async def _clasificar_app(page, url: str) -> str:
    """Abre una app Google y decide, por el estado final del render: 'abre' (público) o
    'pide_cuenta' (login wall). 'no_concluyente' si el render no da una señal clara."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(2500)  # dar tiempo al JS de la SPA
    except Exception:  # noqa: BLE001
        return "no_concluyente"
    destino = page.url.lower()
    if "accounts.google.com" in destino or "servicelogin" in destino:
        return "pide_cuenta"
    try:
        cuerpo = await page.locator("body").inner_text()
    except Exception:  # noqa: BLE001
        return "no_concluyente"
    if _RE_LOGIN_WALL.search(cuerpo[:3000]):
        return "pide_cuenta"
    return "abre"


async def pase_navegador(base: str, user: str, pw: str, quizzes: list[dict],
                         apps: list[str], max_apps: int = 40) -> dict:
    """Corre el pase completo. `quizzes`: [{cmid, clase, nombre, unidad}] (clase = 'mini' |
    'autoeval' | 'otro'). `apps`: URLs de NotebookLM/Colab a clasificar. Devuelve
    {preguntas: {cmid: n|None}, apps: [{url, estado}], login_ok, aviso?}. No rompe: ante
    cualquier fallo devuelve lo que alcanzó a juntar."""
    if not disponible():
        return {"omitido": True, "aviso": "Playwright no está instalado: corré "
                "`pip install playwright && playwright install chromium` para el pase "
                "navegador. La auditoría por API ya corrió sin él."}
    from playwright.async_api import async_playwright

    os.makedirs(os.path.dirname(_STORAGE), exist_ok=True)
    preguntas: dict = {}
    apps_res: list = []
    login_ok = False
    async with async_playwright() as p:
        navegador = await p.chromium.launch(headless=True)
        ctx = await navegador.new_context()
        page = await ctx.new_page()
        try:
            login_ok = await _login(page, base, user, pw)
            if not login_ok:
                return {"login_ok": False, "aviso": "No pude loguear por navegador "
                        "(revisá usuario/contraseña). El pase navegador quedó sin correr."}
            await ctx.storage_state(path=_STORAGE)  # sesión persistida para próximas corridas
            for q in quizzes:
                preguntas[q["cmid"]] = await _contar_preguntas(page, base, q["cmid"])
            for url in apps[:max_apps]:
                apps_res.append({"url": url, "estado": await _clasificar_app(page, url)})
        finally:
            await navegador.close()

    out = {"login_ok": login_ok, "preguntas": preguntas, "apps": apps_res}
    if len(apps) > max_apps:
        out["aviso"] = f"Clasifiqué {max_apps} de {len(apps)} apps Google (tope). Subí max_apps si querés todas."
    return out
