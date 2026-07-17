"""Cliente de la API REST oficial de Moodle vía el servicio `moodle_mobile_app`.

Versión de-un-tutor para la Skill: el token se auto-emite con las credenciales del
tutor (`login/token.php`, sin admin — el servicio mobile está habilitado porque la app
oficial lo usa) y las operaciones se hacen por `webservice/rest/server.php` con JSON
estructurado y estable.

A diferencia del copiloto, acá NO hay `HybridMoodleClient` ni fallback a Playwright:
la Skill corre 100% sobre la API REST con UN solo cliente (las credenciales salen de
env vars del tutor). Se saca toda la dependencia de navegador/SessionPool/multi-tenant.
"""

import asyncio

import httpx

_MOBILE_SERVICE = "moodle_mobile_app"
_HTTP_TIMEOUT_S = 60.0
# Errores REST que significan "el token se pudrió": reemitir y reintentar una vez.
_TOKEN_ERRORS = {"invalidtoken", "accessexception", "servicerequireslogin", "webservicecannotusetoken"}


def _flatten(params: dict) -> dict:
    """Aplana un dict con listas/dicts anidados al formato que Moodle REST espera:
    {"courseids":[38]} -> {"courseids[0]":"38"};
    {"options":[{"name":"a","value":"b"}]} -> {"options[0][name]":"a","options[0][value]":"b"};
    {"plugindata":{"x":{"text":"y"}}} -> {"plugindata[x][text]":"y"}."""
    out: dict[str, str] = {}

    def rec(prefix: str, val) -> None:
        if val is None:
            return  # Moodle REST espera el parámetro AUSENTE, no "None".
        if isinstance(val, dict):
            for k, v in val.items():
                rec(f"{prefix}[{k}]" if prefix else str(k), v)
        elif isinstance(val, (list, tuple)):
            for i, v in enumerate(val):
                rec(f"{prefix}[{i}]", v)
        else:
            if isinstance(val, bool):
                val = 1 if val else 0
            out[prefix] = str(val)

    for k, v in (params or {}).items():
        rec(str(k), v)
    return out


class MoodleWSError(Exception):
    """Error propio devuelto por un web service (no de token). Lleva el errorcode."""

    def __init__(self, fn: str, payload: dict):
        self.fn = fn
        self.errorcode = payload.get("errorcode")
        self.message = payload.get("message") or str(payload)
        super().__init__(f"{fn}: {self.errorcode} — {self.message}")


class MobileWSClient:
    """Token `moodle_mobile_app` (auto-emitido y cacheado) + llamadas REST.

    No mantiene un AsyncClient persistente: cada request usa uno efímero, para no
    arrastrar estado de conexión entre consultas (el mismo patrón del copiloto).

    Se instancia con las credenciales del tutor que corre la Skill:
        MobileWSClient(base_url, usuario, password)
    donde `usuario` es el DNI/usuario de login del campus."""

    def __init__(self, base_url: str, usuario: str, password: str):
        self._base = base_url.rstrip("/")
        self._dni = usuario
        self._password = password
        self._token: str | None = None
        self._userid: int | None = None
        self._lock = asyncio.Lock()

    # `ws_api` fue escrito contra el HybridMoodleClient, que exponía el MobileWSClient
    # en `.api`. Acá el cliente ES el MobileWSClient, así que `.api` devuelve self:
    # así `client.api.userid()` de ws_api funciona sin tocar ws_api.
    @property
    def api(self) -> "MobileWSClient":
        return self

    async def _emitir_token(self) -> None:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
            r = await http.post(
                f"{self._base}/login/token.php",
                data={"username": self._dni, "password": self._password, "service": _MOBILE_SERVICE},
            )
            j = r.json()
        if not j.get("token"):
            raise RuntimeError(f"No pude emitir el token mobile: {j.get('error') or j}")
        self._token = j["token"]
        self._userid = None  # se recalcula en el próximo site_info

    async def _asegurar_token(self) -> None:
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self._emitir_token()

    async def ws(self, fn: str, params: dict | None = None) -> object:
        """Llama un web service REST. Ante token vencido, reemite y reintenta 1 vez.
        Devuelve el JSON parseado. Si el WS devuelve su propio error (no de token),
        lanza MoodleWSError para que el caller lo maneje como dato."""
        await self._asegurar_token()
        for intento in range(2):
            data = {"wstoken": self._token, "wsfunction": fn, "moodlewsrestformat": "json"}
            data.update(_flatten(params or {}))
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
                r = await http.post(f"{self._base}/webservice/rest/server.php", data=data)
            payload = r.json()
            if isinstance(payload, dict) and payload.get("exception"):
                if payload.get("errorcode") in _TOKEN_ERRORS and intento == 0:
                    await self._emitir_token()
                    continue
                raise MoodleWSError(fn, payload)
            return payload
        raise MoodleWSError(fn, {"errorcode": "tokenretryfailed", "message": "token inválido tras reemitir"})

    async def userid(self) -> int:
        """userid del tutor (de core_webservice_get_site_info, cacheado)."""
        if self._userid is None:
            info = await self.ws("core_webservice_get_site_info")
            self._userid = int(info["userid"])
        return self._userid

    async def token_download(self, url: str) -> bytes:
        """Descarga un archivo protegido (pluginfile) agregando el token."""
        await self._asegurar_token()
        sep = "&" if "?" in url else "?"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as http:
            r = await http.get(f"{url}{sep}token={self._token}")
            r.raise_for_status()
            return r.content
