# TUP Campus Navigator

Skill de [Claude Code](https://claude.com/claude-code) para **tutores** de la
Tecnicatura Universitaria en Programación (UTN) que operan el campus Moodle
`https://tup.sied.utn.edu.ar`. Ver pendientes, generar informes, cargar notas y
(opcional) corregir con **Active-IA** — todo por la **API REST oficial** de Moodle,
desde la terminal.

> Habla con Moodle por **peticiones REST**, no por navegador: no se rompe cuando el
> campus cambia el HTML, y verifica cada dato en vivo — nunca inventa un ID.

## ¿Qué hace?

Un tutor le habla a Claude Code en castellano y la skill opera el campus por él:

- **Mapea tus comisiones** (una sola vez): descubre en vivo tus cursos, comisiones y
  tareas, y valida cada `group_id` contra el campus antes de guardarlo.
- **Snapshot on-demand**: cuando se lo pedís, releva quién entregó y qué falta
  corregir, por comisión, en todos tus cursos (Prog I, II y III).
- **Mensajes y foros**: qué alumnos te escribieron y qué posts están sin responder, con
  la respuesta previsualizada antes de publicarla.
- **Auditoría de aula**: chequea que el aula esté bien armada (presencia, ausencia y
  consistencia de actividades), con un pase por navegador para lo que la API no expone.
- **Informes en PDF** de pendientes por comisión.
- **Ver la entrega antes de calificar**: baja el trabajo del alumno, descomprime el `.zip`
  y te muestra el código. Calificar sin haber visto lo entregado deja de ser posible.
- **Carga notas** con su devolución, mostrándote qué va a escribir y esperando tu OK.
  Después de escribir **relee la nota** para confirmar que quedó guardada de verdad.
- **Corrección automática con Active-IA** (opcional): baja el trabajo del alumno,
  lo corrige con IA (Gemini, contra la rúbrica) y **te descarga el PDF de devolución**
  en tu carpeta `salidas/`. Todo con tu OK antes de escribir la nota.

Reusa la lógica de API REST ya probada en producción (token `moodle_mobile_app`,
`mod_assign_*`), empaquetada para correr **local**: cada tutor con sus credenciales,
sin depender de ningún servidor central.

## Instalación (un comando)

```bash
git clone https://github.com/Group-Active-IA/Skill-Moodle.git ~/.claude/skills/tup-campus-navigator
bash ~/.claude/skills/tup-campus-navigator/install.sh
```

El `install.sh` hace todo solo: crea un entorno Python aislado (no toca tu Python del
sistema), instala las dependencias del MCP, y **enchufa el MCP a Claude Code con
`claude mcp add`** — sin que tengas que editar ningún JSON. Después **reiniciá Claude
Code** para que cargue el MCP.

> Instalación manual (si preferís): `pip install -r mcp/requirements.txt` en un venv,
> y `claude mcp add moodle-tutor -s user -- <python-del-venv> <ruta>/mcp/server.py`. El
> bloque JSON equivalente está en `mcp/config.example.json`.

**Las credenciales no se setean a mano.** En la primera sesión, decile a Claude tu
usuario y contraseña de Moodle:

> *"Configurá mis credenciales: usuario 12345678, contraseña …"*

Claude llama la tool `configurar`, que valida el login contra el campus y guarda las
credenciales en `~/.moodle-skill/.env` (permisos 600, fuera del repo — nunca se sube a
git). Si vas a usar Active-IA, pasale también ese usuario y contraseña. Listo: no
tocás variables de entorno.

> El `.env` local tiene tu contraseña en texto: es tuyo, en tu máquina, con permisos
> 600. No lo compartas ni lo subas a ningún lado.

## Actualizar la skill

La skill vive en un clon local en tu máquina, así que no se actualiza sola. Para no
quedarte meses atrás sin enterarte, avisa cuando hay una versión nueva:

- Al consultar **`mis_datos`** (lo primero que hace la skill en cada sesión) aparece un
  `actualizacion_disponible` si salió una versión posterior a la tuya. El chequeo se
  cachea 24 h, así que no agrega demora.
- *"¿Qué versión de la skill tengo?"* → tool `version_skill`, que compara tu `VERSION`
  local contra el publicado en GitHub.
- *"Actualizá la skill"* → tool `actualizar_skill`: hace `git pull --ff-only` y te dice
  si hay que reinstalar dependencias.

```bash
# equivalente a mano
cd ~/.claude/skills/tup-campus-navigator && git pull
```

> Si tenés cambios sin commitear en la carpeta de la skill, `actualizar_skill` **no toca
> nada** y te los lista: pisar tu trabajo sería peor que quedar desactualizado.

**Después de actualizar hay que reiniciar Claude Code.** El MCP se carga al arrancar la
sesión: hasta que no reinicies seguís usando la versión vieja aunque los archivos ya
estén nuevos.

## Uso

En una sesión de Claude Code, decí algo como:

- *"Mapeá mis comisiones"* → la skill descubre y guarda tu cohorte (Paso 0).
- *"¿Qué me falta corregir?"* → corre el snapshot y te da los pendientes.
- *"Hacé el informe en PDF de la comisión 23"* → genera el PDF.
- *"Ponele Aprobado al TP de tal alumno"* → carga la nota tras tu OK.

La primera vez **siempre** hay que mapear (Paso 0 — Bootstrap en `SKILL.md`); sin eso
la skill no sabe cuáles son tus comisiones.

## Estructura

```
Skill-Moodle/
├── SKILL.md                  # Lógica que sigue el agente (doctrina + reglas)
├── README.md                 # Este archivo
├── VERSION                   # Versión publicada (la compara `version_skill`)
├── LICENSE                   # Apache-2.0
├── mcp/                      # MCP server liviano (API REST)
│   ├── server.py             # 31 tools (configurar, aulas, mensajes, foros, snapshot, informe, auditoría, ver_entrega, cargar_nota, Active-IA…)
│   ├── aulas.json            # Catálogo materia→curso de la cohorte vigente
│   ├── comisiones.json       # Catálogo tutor→comisión y cmid de actividades por materia
│   ├── requirements.txt
│   ├── config.example.json   # Bloque mcpServers para Claude Code
│   └── moodle/
│       ├── cliente.py        # MobileWSClient (token + REST)
│       ├── ws_api.py         # Operaciones REST
│       ├── snapshot.py       # Relevo on-demand, multi-curso
│       ├── informes.py       # PDF (reportlab)
│       ├── auditoria.py      # Auditoría de aula por REST (presencia/ausencia/consistencia)
│       ├── navegador.py      # Pase con Playwright para lo que la API REST no expone
│       ├── active_ia.py      # Cliente de la API de Active-IA (corrección con Gemini)
│       ├── version.py        # Chequeo de versión nueva + actualización por git
│       └── almacen.py        # Persistencia local (mis_datos.json + SQLite)
├── install.sh               # Instalador de un comando (venv + claude mcp add)
└── references/
    └── active-ia.md          # Pipeline de corrección automática con Active-IA
```

## Por qué esta estructura

- **MCP local, no servidor central.** Cada tutor corre lo suyo con sus credenciales.
  Portable, sin cuentas que administrar, sin depender de un backend.
- **API REST, no navegador.** JSON estructurado en vez de scraping de HTML: más
  rápido y no se rompe con cambios de diseño del campus.
- **Validar, no inventar.** El mapeo valida cada `group_id` contra el campus real —
  la defensa contra IDs alucinados que corrompen los datos en silencio.
- **`references/` para lo pesado.** Active-IA y el flujo viejo viven fuera del
  `SKILL.md` (que se mantiene < 500 líneas), con pointers de cuándo cargarlos.

## Licencia

Apache-2.0
