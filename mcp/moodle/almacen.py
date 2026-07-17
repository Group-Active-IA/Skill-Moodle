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

CREATE INDEX IF NOT EXISTS idx_snapshots_fecha ON snapshots(fecha);
CREATE INDEX IF NOT EXISTS idx_snapshots_com_assign ON snapshots(comision, assign_id);
CREATE INDEX IF NOT EXISTS idx_entregas_email ON entregas(email);
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
