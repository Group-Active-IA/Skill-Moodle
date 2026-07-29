"""Persistencia LOCAL y de-un-tutor de la Skill (reemplaza el `db` multi-tenant del
copiloto).

El copiloto guardaba todo en un SQLite compartido con scoping por `tutor_id` (vault de
credenciales, sesiones del SDK, conversaciones, roles…). Nada de eso aplica a la Skill:
acá corre UN tutor con SUS credenciales de env vars. Así que este módulo se queda con lo
mínimo que el snapshot + las tools necesitan y lo persiste local:

- `mis_datos.json`  -> la config del tutor (cursos/comisiones/tareas). Human-editable.
- `datos.db`        -> SQLite con snapshots, caché de entregas y caché de alumnos.

Ubicación: `$MOODLE_SKILL_HOME` (default `~/.moodle-skill`). Se corre cada query en un
thread (asyncio.to_thread) para no bloquear el loop, igual que el `db` original.
"""

import asyncio
import datetime
import json
import os
import sqlite3
from typing import Any

# Raíz de datos de la Skill. Configurable por env para no clavarla en $HOME (tests, CI).
HOME = os.path.expanduser(os.environ.get("MOODLE_SKILL_HOME", "~/.moodle-skill"))
DB_PATH = os.path.join(HOME, "datos.db")
MIS_DATOS_PATH = os.path.join(HOME, "mis_datos.json")
# Directorio de salidas (informes PDF, entregas bajadas).
SALIDAS_DIR = os.path.join(HOME, "salidas")


def _ahora() -> str:
    return datetime.datetime.now().isoformat()


def _conectar() -> sqlite3.Connection:
    os.makedirs(HOME, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# Esquema mínimo: sin tutor_id, sin tablas de auth/vault/conversaciones (eso era del
# copiloto multi-tenant). Solo lo que alimenta buscar_alumno y los tableros.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha         TEXT,
    comision      TEXT,
    assign_id     TEXT,
    tarea         TEXT,
    participantes INTEGER,
    entregados    INTEGER,
    calificados   INTEGER,
    pendientes    INTEGER,
    datos_json    TEXT
);

CREATE TABLE IF NOT EXISTS alumnos (
    email          TEXT PRIMARY KEY,
    nombre         TEXT,
    comision       TEXT,
    ultimo_acceso  TEXT,
    actualizado_at TEXT
);

CREATE TABLE IF NOT EXISTS entregas (
    email     TEXT,
    comision  TEXT,
    assign_id TEXT,
    tarea     TEXT,
    estado    TEXT,
    nota      TEXT,
    pendiente INTEGER
);

-- Bitácora de correcciones. A diferencia de `entregas` (que es una foto del estado
-- actual y se pisa entera en cada snapshot), esto es HISTÓRICO y sólo crece: cada nota
-- cargada queda con su devolución y los temas que se le marcaron al alumno.
-- Para qué: cuando media comisión falla en lo mismo, el problema no son los alumnos —
-- es que ese tema no quedó bien explicado. Ese dato sólo se puede ver acumulando, y se
-- pierde para siempre si no se guarda en el momento de corregir.
CREATE TABLE IF NOT EXISTS correcciones (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha         TEXT,
    course_id     INTEGER,
    assign_id     TEXT,
    tarea         TEXT,
    comision      TEXT,
    email         TEXT,
    alumno        TEXT,
    nota          TEXT,
    devolucion    TEXT,
    etiquetas     TEXT   -- JSON: ["perimetro-circulo", "conversion-unidades"]
);

-- Cola de una sesión de corrección. Se va llenando alumno por alumno SIN tocar Moodle, y
-- se escribe todo junto al final con una sola confirmación. Es persistente a propósito:
-- corregir 15 TPs no entra en una sentada, y si se corta la sesión el trabajo hecho no se
-- puede perder.
CREATE TABLE IF NOT EXISTS cola_correccion (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    creada_at  TEXT,
    assign_id  TEXT,
    tarea      TEXT,
    group_id   INTEGER,
    comision   TEXT,
    email      TEXT,
    alumno     TEXT,
    nota       TEXT,
    devolucion TEXT,
    etiquetas  TEXT,
    estado     TEXT,   -- pendiente | anotado | escrito | error
    resultado  TEXT,   -- detalle del error si estado = 'error'
    UNIQUE(assign_id, group_id, email)
);

CREATE INDEX IF NOT EXISTS idx_cola_estado ON cola_correccion(estado);
CREATE INDEX IF NOT EXISTS idx_snapshots_fecha ON snapshots(fecha);
CREATE INDEX IF NOT EXISTS idx_snapshots_com_assign ON snapshots(comision, assign_id);
CREATE INDEX IF NOT EXISTS idx_entregas_email ON entregas(email);
CREATE INDEX IF NOT EXISTS idx_correcciones_assign ON correcciones(assign_id, comision);
CREATE INDEX IF NOT EXISTS idx_correcciones_curso ON correcciones(course_id);
"""


def _init_db() -> None:
    os.makedirs(SALIDAS_DIR, exist_ok=True)
    con = _conectar()
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


async def init_db() -> None:
    """Crea el directorio de datos + las tablas si no existen."""
    await asyncio.to_thread(_init_db)


# --- "Mis datos": config del tutor en un JSON local (human-editable) ---

def _get_mis_datos() -> dict | None:
    try:
        with open(MIS_DATOS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return None


async def get_mis_datos() -> dict | None:
    return await asyncio.to_thread(_get_mis_datos)


def _set_mis_datos(datos: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    with open(MIS_DATOS_PATH, "w", encoding="utf-8") as fh:
        json.dump(datos, fh, ensure_ascii=False, indent=2)


async def set_mis_datos(datos: dict) -> None:
    await asyncio.to_thread(_set_mis_datos, datos)


async def mis_datos_actualizada() -> str | None:
    """mtime del mis_datos.json (ISO), o None si no existe."""
    def _stat() -> str | None:
        try:
            ts = os.path.getmtime(MIS_DATOS_PATH)
        except OSError:
            return None
        return datetime.datetime.fromtimestamp(ts).isoformat()

    return await asyncio.to_thread(_stat)


# --- snapshots ---

def _guardar_snapshot(fila: dict[str, Any]) -> int:
    datos = fila.get("datos_json")
    if datos is not None and not isinstance(datos, str):
        datos = json.dumps(datos, ensure_ascii=False)
    con = _conectar()
    try:
        cur = con.execute(
            "INSERT INTO snapshots "
            "(fecha, comision, assign_id, tarea, participantes, entregados, "
            "calificados, pendientes, datos_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fila.get("fecha") or _ahora(),
                fila.get("comision"),
                fila.get("assign_id"),
                fila.get("tarea"),
                fila.get("participantes"),
                fila.get("entregados"),
                fila.get("calificados"),
                fila.get("pendientes"),
                datos,
            ),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


async def guardar_snapshot(fila: dict[str, Any]) -> int:
    return await asyncio.to_thread(_guardar_snapshot, fila)


def _ultimo_snapshot() -> list[dict]:
    """Último snapshot por (comision, assign_id), tomando el id más reciente."""
    con = _conectar()
    try:
        cur = con.execute(
            "SELECT s.* FROM snapshots s "
            "JOIN (SELECT comision, assign_id, MAX(id) AS mid FROM snapshots "
            "GROUP BY comision, assign_id) u "
            "ON s.id = u.mid "
            "ORDER BY s.comision, s.assign_id"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


async def ultimo_snapshot() -> list[dict]:
    return await asyncio.to_thread(_ultimo_snapshot)


# --- entregas (caché por alumno para la traza, llenada por el snapshot) ---

def _reemplazar_entregas(filas: list[dict]) -> int:
    """Reescribe TODA la caché de entregas en una transacción (delete + insert).
    Single-tenant: es la del único tutor, así que se borra completa y se reinserta."""
    con = _conectar()
    try:
        con.execute("DELETE FROM entregas")
        con.executemany(
            "INSERT INTO entregas "
            "(email, comision, assign_id, tarea, estado, nota, pendiente) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    (f.get("email") or "").lower(),
                    f.get("comision"),
                    f.get("assign_id"),
                    f.get("tarea"),
                    f.get("estado"),
                    f.get("nota"),
                    1 if f.get("pendiente") else 0,
                )
                for f in filas
            ],
        )
        con.commit()
        return len(filas)
    finally:
        con.close()


async def reemplazar_entregas(filas: list[dict]) -> int:
    return await asyncio.to_thread(_reemplazar_entregas, filas)


def _entregas_previas(comision: str, assign_id: str) -> list[dict]:
    """Filas de entregas ya cacheadas de una (comisión, tarea). Sirve para REUSAR el
    dato previo cuando una tarea falla en el snapshot (no perderlo por un timeout)."""
    con = _conectar()
    try:
        rows = con.execute(
            "SELECT email, comision, assign_id, tarea, estado, nota, pendiente "
            "FROM entregas WHERE comision = ? AND assign_id = ?",
            (comision, str(assign_id)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


async def entregas_previas(comision: str, assign_id: str) -> list[dict]:
    return await asyncio.to_thread(_entregas_previas, comision, assign_id)


# --- alumnos ---

def _upsert_alumno(
    email: str,
    nombre: str | None = None,
    comision: str | None = None,
    ultimo_acceso: str | None = None,
) -> None:
    con = _conectar()
    try:
        con.execute(
            "INSERT INTO alumnos (email, nombre, comision, ultimo_acceso, actualizado_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET "
            "nombre = COALESCE(excluded.nombre, alumnos.nombre), "
            "comision = COALESCE(excluded.comision, alumnos.comision), "
            "ultimo_acceso = COALESCE(excluded.ultimo_acceso, alumnos.ultimo_acceso), "
            "actualizado_at = excluded.actualizado_at",
            (email.lower(), nombre, comision, ultimo_acceso, _ahora()),
        )
        con.commit()
    finally:
        con.close()


async def upsert_alumno(
    email: str,
    nombre: str | None = None,
    comision: str | None = None,
    ultimo_acceso: str | None = None,
) -> None:
    await asyncio.to_thread(_upsert_alumno, email, nombre, comision, ultimo_acceso)


def _traza_alumno(email: str) -> dict | None:
    """Identidad (de alumnos) + entregas por tarea (de entregas), con el shape que
    consume buscar_alumno: {nombre, comision, ultimo_acceso, entregas[], pendientes[]}."""
    em = email.lower()
    con = _conectar()
    try:
        ident = con.execute("SELECT * FROM alumnos WHERE email = ?", (em,)).fetchone()
        rows = con.execute(
            "SELECT tarea, estado, nota, pendiente FROM entregas "
            "WHERE email = ? ORDER BY rowid",
            (em,),
        ).fetchall()
        if ident is None and not rows:
            return None
        ident = dict(ident) if ident else {}
        return {
            "email": em,
            "nombre": ident.get("nombre") or em,
            "comision": ident.get("comision"),
            "ultimo_acceso": ident.get("ultimo_acceso"),
            "entregas": [
                {"tarea": r["tarea"], "estado": r["estado"], "nota": r["nota"]}
                for r in rows
            ],
            "pendientes": [r["tarea"] for r in rows if r["pendiente"]],
        }
    finally:
        con.close()


async def traza_alumno(email: str) -> dict | None:
    return await asyncio.to_thread(_traza_alumno, email)


# --- cola de corrección (sesión en curso) ---

def _cola_abrir(assign_id: str, tarea: str, group_id: int, comision: str | None,
                alumnos: list[dict], reemplazar: bool) -> dict:
    con = _conectar()
    try:
        if reemplazar:
            con.execute("DELETE FROM cola_correccion WHERE assign_id = ? AND group_id = ?",
                        (str(assign_id), group_id))
        nuevos = 0
        for a in alumnos:
            # INSERT OR IGNORE: si el alumno ya estaba en la cola (sesión retomada) se
            # conserva lo que se le había anotado en vez de pisarlo con 'pendiente'.
            cur = con.execute(
                "INSERT OR IGNORE INTO cola_correccion (creada_at, assign_id, tarea, "
                "group_id, comision, email, alumno, estado) VALUES (?,?,?,?,?,?,?,'pendiente')",
                (_ahora(), str(assign_id), tarea, group_id, comision,
                 (a.get("email") or "").lower(), a.get("nombre")))
            nuevos += cur.rowcount
        con.commit()
        tot = con.execute(
            "SELECT COUNT(*) c FROM cola_correccion WHERE assign_id=? AND group_id=?",
            (str(assign_id), group_id)).fetchone()["c"]
    finally:
        con.close()
    return {"en_cola": tot, "agregados": nuevos}


def _cola_siguiente(assign_id: str | None, group_id: int | None) -> dict | None:
    where = "estado = 'pendiente'"
    params: list = []
    if assign_id:
        where += " AND assign_id = ?"; params.append(str(assign_id))
    if group_id is not None:
        where += " AND group_id = ?"; params.append(group_id)
    con = _conectar()
    try:
        f = con.execute(f"SELECT * FROM cola_correccion WHERE {where} ORDER BY id LIMIT 1",
                        params).fetchone()
        return dict(f) if f else None
    finally:
        con.close()


def _cola_anotar(assign_id: str, group_id: int, email: str, nota: str,
                 devolucion: str, etiquetas: list) -> bool:
    """Anota (o RE-anota) la corrección de un alumno de la cola.

    Acepta también las filas en estado 'error' y 'salteado', y ese detalle no es menor:
    `confirmar_cola` promete "las fallidas quedaron en la cola, arreglá el motivo y volvé a
    confirmar", pero si acá sólo se aceptaran 'pendiente'/'anotado' esa promesa sería
    mentira — la fila fallida quedaría en un estado del que no se puede salir y el tutor no
    tendría forma de corregir el error que la propia herramienta le señaló.
    Las 'escrito' NO se aceptan a propósito: para cambiar una nota ya cargada está
    `cargar_nota`, y reabrirlas acá permitiría duplicar escrituras sin querer."""
    con = _conectar()
    try:
        cur = con.execute(
            "UPDATE cola_correccion SET nota=?, devolucion=?, etiquetas=?, "
            "estado='anotado', resultado=NULL "
            "WHERE assign_id=? AND group_id=? AND email=? "
            "AND estado IN ('pendiente','anotado','error','salteado')",
            (nota, devolucion, json.dumps(etiquetas or [], ensure_ascii=False),
             str(assign_id), group_id, (email or "").lower()))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def _cola_saltear(assign_id: str, group_id: int, email: str, motivo: str) -> bool:
    """Saca a un alumno de la cola SIN calificarlo.

    Hace falta porque no todo lo que está pendiente se puede corregir: alguien que subió
    el archivo equivocado (pasó: un alumno entregó los apuntes de la cátedra en vez de su
    TP) no merece ni Aprobado ni Desaprobado — necesita que le avisen. Sin esta salida, la
    cola devolvía siempre a la misma persona y la única forma de avanzar era ponerle una
    nota que no correspondía."""
    con = _conectar()
    try:
        cur = con.execute(
            "UPDATE cola_correccion SET estado='salteado', resultado=? "
            "WHERE assign_id=? AND group_id=? AND email=? AND estado IN ('pendiente','anotado')",
            (motivo, str(assign_id), group_id, (email or "").lower()))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


async def cola_saltear(assign_id, group_id, email, motivo):
    return await asyncio.to_thread(_cola_saltear, assign_id, group_id, email, motivo)


def _cola_listar(assign_id: str | None = None, group_id: int | None = None,
                 estados: tuple = ("pendiente", "anotado", "escrito", "error")) -> list[dict]:
    where = f"estado IN ({','.join('?' * len(estados))})"
    params: list = list(estados)
    if assign_id:
        where += " AND assign_id = ?"; params.append(str(assign_id))
    if group_id is not None:
        where += " AND group_id = ?"; params.append(group_id)
    con = _conectar()
    try:
        filas = con.execute(
            f"SELECT * FROM cola_correccion WHERE {where} ORDER BY id", params).fetchall()
    finally:
        con.close()
    out = []
    for f in filas:
        d = dict(f)
        try:
            d["etiquetas"] = json.loads(d.get("etiquetas") or "[]")
        except (ValueError, TypeError):
            d["etiquetas"] = []
        out.append(d)
    return out


def _cola_marcar(fila_id: int, estado: str, resultado: str | None = None) -> None:
    con = _conectar()
    try:
        con.execute("UPDATE cola_correccion SET estado=?, resultado=? WHERE id=?",
                    (estado, resultado, fila_id))
        con.commit()
    finally:
        con.close()


def _cola_limpiar(assign_id: str | None = None, group_id: int | None = None) -> int:
    where, params = "1=1", []
    if assign_id:
        where += " AND assign_id = ?"; params.append(str(assign_id))
    if group_id is not None:
        where += " AND group_id = ?"; params.append(group_id)
    con = _conectar()
    try:
        cur = con.execute(f"DELETE FROM cola_correccion WHERE {where}", params)
        con.commit()
        return cur.rowcount
    finally:
        con.close()


async def cola_abrir(assign_id, tarea, group_id, comision, alumnos, reemplazar=False):
    return await asyncio.to_thread(_cola_abrir, assign_id, tarea, group_id, comision,
                                   alumnos, reemplazar)


async def cola_siguiente(assign_id=None, group_id=None):
    return await asyncio.to_thread(_cola_siguiente, assign_id, group_id)


async def cola_anotar(assign_id, group_id, email, nota, devolucion, etiquetas):
    return await asyncio.to_thread(_cola_anotar, assign_id, group_id, email, nota,
                                   devolucion, etiquetas)


async def cola_listar(assign_id=None, group_id=None, estados=("pendiente", "anotado",
                                                              "escrito", "error")):
    return await asyncio.to_thread(_cola_listar, assign_id, group_id, estados)


async def cola_marcar(fila_id, estado, resultado=None):
    await asyncio.to_thread(_cola_marcar, fila_id, estado, resultado)


async def cola_limpiar(assign_id=None, group_id=None):
    return await asyncio.to_thread(_cola_limpiar, assign_id, group_id)


# --- correcciones (bitácora histórica) ---

def _guardar_correccion(reg: dict) -> None:
    con = _conectar()
    try:
        con.execute(
            "INSERT INTO correcciones (fecha, course_id, assign_id, tarea, comision, "
            "email, alumno, nota, devolucion, etiquetas) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_ahora(), reg.get("course_id"), str(reg.get("assign_id") or ""),
             reg.get("tarea"), reg.get("comision"), (reg.get("email") or "").lower(),
             reg.get("alumno"), reg.get("nota"), reg.get("devolucion"),
             json.dumps(reg.get("etiquetas") or [], ensure_ascii=False)),
        )
        con.commit()
    finally:
        con.close()


async def guardar_correccion(reg: dict) -> None:
    await asyncio.to_thread(_guardar_correccion, reg)


# Correcciones mínimas para que un porcentaje signifique algo. Por debajo de esto no se
# marca nada como sistémico: con 2 corregidos, 1 error da 50% y sugeriría rehacer la clase
# por una sola persona.
_MUESTRA_MINIMA = 5


def _errores_frecuentes(course_id: int | None = None, assign_id: str | None = None,
                        comision: str | None = None) -> dict:
    """Agrega las etiquetas de las correcciones ya hechas.

    El porcentaje se calcula sobre los alumnos CORREGIDOS, no sobre los que tienen el
    error: lo que importa pedagógicamente no es "8 alumnos se equivocaron" sino "8 de 12",
    que es cuando deja de ser un problema individual."""
    where, params = [], []
    if course_id:
        where.append("course_id = ?"); params.append(course_id)
    if assign_id:
        where.append("assign_id = ?"); params.append(str(assign_id))
    if comision:
        where.append("comision = ?"); params.append(comision)
    sql = "SELECT tarea, assign_id, comision, alumno, nota, etiquetas FROM correcciones"
    if where:
        sql += " WHERE " + " AND ".join(where)

    con = _conectar()
    try:
        filas = con.execute(sql, params).fetchall()
    finally:
        con.close()

    corregidas = len(filas)
    conteo: dict[str, list] = {}
    for f in filas:
        try:
            etiquetas = json.loads(f["etiquetas"] or "[]")
        except (ValueError, TypeError):
            etiquetas = []
        for e in etiquetas:
            conteo.setdefault(str(e), []).append(f["alumno"])

    items = []
    for etiqueta, alumnos in conteo.items():
        pct = round(100 * len(alumnos) / corregidas) if corregidas else 0
        items.append({
            "tema": etiqueta,
            "alumnos_afectados": len(alumnos),
            "de_corregidos": corregidas,
            "porcentaje": pct,
            # Un porcentaje sobre 2 correcciones no significa nada: 1 de 2 da 50% y
            # marcaría "reforzalo con toda la comisión" porque una persona se equivocó.
            # Recién con MUESTRA_MINIMA el número empieza a decir algo.
            "sistemico": pct >= 40 and corregidas >= _MUESTRA_MINIMA,
            "quienes": sorted(a for a in alumnos if a)[:12],
        })
    items.sort(key=lambda i: -i["alumnos_afectados"])
    return {"correcciones_registradas": corregidas, "temas": items,
            "muestra_suficiente": corregidas >= _MUESTRA_MINIMA,
            "muestra_minima": _MUESTRA_MINIMA}


async def errores_frecuentes(course_id: int | None = None, assign_id: str | None = None,
                             comision: str | None = None) -> dict:
    return await asyncio.to_thread(_errores_frecuentes, course_id, assign_id, comision)


def _buscar_alumnos(texto: str, limite: int = 8) -> list[dict]:
    """Busca alumnos por NOMBRE o email (substring) en el caché y devuelve la traza de
    cada coincidencia. Si el alumno no tiene entregas cacheadas, 'entregas' viene vacío
    pero 'comision' indica en qué comisión está."""
    q = f"%{texto.lower().strip()}%"
    con = _conectar()
    try:
        rows = con.execute(
            "SELECT email FROM alumnos WHERE lower(nombre) LIKE ? OR lower(email) LIKE ? "
            "ORDER BY nombre LIMIT ?",
            (q, q, limite),
        ).fetchall()
    finally:
        con.close()
    res = [_traza_alumno(r["email"]) for r in rows]
    return [t for t in res if t]


async def buscar_alumnos(texto: str, limite: int = 8) -> list[dict]:
    return await asyncio.to_thread(_buscar_alumnos, texto, limite)
