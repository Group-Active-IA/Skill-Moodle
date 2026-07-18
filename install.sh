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
