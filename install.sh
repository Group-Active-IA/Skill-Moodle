#!/usr/bin/env bash
# Instalador de la skill TUP Campus Navigator — un comando y queda todo listo.
# Uso:  bash install.sh
# Necesitás: acceso al repo Group-Active-IA/Skill-Moodle, Claude Code, y python3.
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
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo
  echo "✗ Tenés Python $PYVER y la skill necesita 3.10 o más nuevo."
  echo "  Instalá una versión más nueva y volvé a correr este script. Por ejemplo:"
  echo "    Ubuntu/Debian:  sudo apt install python3.12 python3.12-venv"
  echo "    macOS (brew):   brew install python@3.12"
  echo "  Si ya tenés otra instalada, corré el script con ella:"
  echo "    python3.12 -m venv \"$VENV\" && bash install.sh"
  exit 1
fi
echo "   (Python $PYVER ✓)"
python3 -m venv "$VENV"

echo "→ 3/4  Instalando las dependencias del MCP en ese entorno…"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$DIR/mcp/requirements.txt"

echo "→ 4/4  Enchufando el MCP a Claude Code (sin tocar ningún JSON)…"
claude mcp remove moodle-tutor -s user >/dev/null 2>&1 || true
claude mcp add moodle-tutor -s user \
  -e MOODLE_URL=https://tup.sied.utn.edu.ar \
  -- "$VENV/bin/python" "$DIR/mcp/server.py"

echo
echo "✓ Listo. Reiniciá Claude Code y decile:  «entrá al campus TUP»"
echo "  La primera vez te va a pedir tus credenciales de Moodle y a mapear tus comisiones."
