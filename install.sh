#!/usr/bin/env bash
# Instalador de la skill TUP Campus Navigator — un comando y queda todo listo.
# Uso:  bash install.sh
# Necesitás: acceso al repo Group-Active-IA/Skill-Moodle, Claude Code, y Python 3.10+.
#
# Plataformas: Linux, macOS, WSL y Windows con Git Bash. En Windows NO corre desde
# PowerShell ni CMD (es un script de bash): abrilo con Git Bash, que viene con Git
# para Windows. El resto lo resuelve solo.
set -euo pipefail

REPO="https://github.com/Group-Active-IA/Skill-Moodle.git"
DIR="$HOME/.claude/skills/tup-campus-navigator"
VENV="$DIR/.venv"

echo "→ 1/4  Bajando la skill…"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --quiet
else
  git clone --quiet "$REPO" "$DIR"
fi

echo "→ 2/4  Creando un entorno Python aislado (no toca tu Python del sistema)…"
# El MCP usa sintaxis de tipos de PEP 604 (`str | None`), que existe recién desde Python
# 3.10. Sin este chequeo el venv se crea, las dependencias instalan bien, y el fallo recién
# aparece al arrancar Claude Code como un SyntaxError opaco que no dice qué falta. En un
# parque de máquinas variadas (Ubuntu 20.04, Python del sistema en Mac, WSL viejo) eso es
# una tarde perdida por persona.
#
# El nombre del intérprete cambia por plataforma: `python3` en Linux/Mac, y en Windows
# normalmente `python` o el launcher `py`. Se prueban los tres y se toma el PRIMERO que
# además cumpla 3.10+: así una máquina con un python2 viejo colgado del PATH no gana.
PY_CMD=()
PYVER=""
for cand in "python3" "python" "py -3"; do
  bin="${cand%% *}"
  command -v "$bin" >/dev/null 2>&1 || continue
  # shellcheck disable=SC2086  # el split de $cand es a propósito ("py -3" son dos palabras)
  if $cand -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    read -r -a PY_CMD <<< "$cand"
    PYVER="$($cand -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    break
  fi
done

if [ ${#PY_CMD[@]} -eq 0 ]; then
  echo
  echo "✗ No encontré un Python 3.10 o más nuevo (probé: python3, python, py -3)."
  echo "  La skill no arranca sin eso. Instalá uno y volvé a correr este script:"
  echo "    Ubuntu/Debian:  sudo apt install python3.12 python3.12-venv"
  echo "    macOS (brew):   brew install python@3.12"
  echo "    Windows:        https://www.python.org/downloads/  (tildá «Add to PATH»)"
  exit 1
fi
echo "   (Python $PYVER ✓)"
"${PY_CMD[@]}" -m venv "$VENV"

# El layout del venv cambia por plataforma: POSIX crea `bin/`, Windows crea `Scripts/`
# con ejecutables `.exe`. Se detecta DESPUÉS de crearlo en vez de adivinar por $OSTYPE,
# porque Git Bash sobre Windows reporta `msys` y WSL reporta `linux` — y los dos corren
# este mismo script con layouts distintos. Preguntarle al filesystem no se equivoca.
if [ -e "$VENV/bin/python" ]; then
  VPY="$VENV/bin/python"
  VPIP="$VENV/bin/pip"
elif [ -e "$VENV/Scripts/python.exe" ]; then
  VPY="$VENV/Scripts/python.exe"
  VPIP="$VENV/Scripts/pip.exe"
else
  echo
  echo "✗ Se creó el entorno en $VENV pero no encontré su Python"
  echo "  (ni en bin/ ni en Scripts/). Borralo y volvé a intentar:"
  echo "    rm -rf \"$VENV\""
  exit 1
fi

echo "→ 3/4  Instalando las dependencias del MCP en ese entorno…"
"$VPIP" install -q --upgrade pip
"$VPIP" install -q -r "$DIR/mcp/requirements.txt"

echo "→ 4/4  Enchufando el MCP a Claude Code (sin tocar ningún JSON)…"
# Bajo Git Bash las rutas son estilo POSIX (/c/Users/…), pero el `claude` que corre es el
# binario de Windows y espera C:\Users\…. `cygpath` sólo existe en ese entorno, así que su
# presencia ES la señal de que hay que traducir. En Linux/Mac/WSL no está y no se toca nada.
MCP_PY="$VPY"
MCP_SERVER="$DIR/mcp/server.py"
if command -v cygpath >/dev/null 2>&1; then
  MCP_PY="$(cygpath -w "$VPY")"
  MCP_SERVER="$(cygpath -w "$MCP_SERVER")"
fi

claude mcp remove moodle-tutor -s user >/dev/null 2>&1 || true
claude mcp add moodle-tutor -s user \
  -e MOODLE_URL=https://tup.sied.utn.edu.ar \
  -- "$MCP_PY" "$MCP_SERVER"

echo
echo "✓ Listo. Reiniciá Claude Code y decile:  «entrá al campus TUP»"
echo "  La primera vez te va a pedir tus credenciales de Moodle y a mapear tus comisiones."
