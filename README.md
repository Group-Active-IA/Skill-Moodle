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
- **Informes en PDF** de pendientes por comisión.
- **Carga notas** con su devolución, mostrándote qué va a escribir y esperando tu OK.
- **Active-IA** (opcional): importar entregas → corregir con IA → devolver la nota.

Reusa la lógica de API REST ya probada en producción (token `moodle_mobile_app`,
`mod_assign_*`), empaquetada para correr **local**: cada tutor con sus credenciales,
sin depender de ningún servidor central.

## Instalación

```bash
npx skills add https://github.com/Group-Active-IA/Skill-Moodle
```

La skill trae un MCP server (`mcp/`). Para activarlo:

```bash
pip install -r mcp/requirements.txt
```

Después conectá el MCP en la config de Claude Code (bloque listo en
`mcp/config.example.json`) y seteá tus credenciales como variables de entorno:

```bash
export MOODLE_URL="https://tup.sied.utn.edu.ar"
export MOODLE_USER="tu-usuario-de-moodle"   # para muchos es el DNI, no para todos
export MOODLE_PASS="tu-contraseña"
```

Tu contraseña **nunca** se escribe a disco: solo se usa para pedir el token.

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
├── LICENSE                   # Apache-2.0
├── mcp/                      # MCP server liviano (API REST)
│   ├── server.py             # 11 tools (descubrir, snapshot, informe, cargar_nota…)
│   ├── requirements.txt
│   ├── config.example.json   # Bloque mcpServers para Claude Code
│   └── moodle/
│       ├── cliente.py        # MobileWSClient (token + REST)
│       ├── ws_api.py         # Operaciones REST
│       ├── snapshot.py       # Relevo on-demand, multi-curso
│       ├── informes.py       # PDF (reportlab)
│       └── almacen.py        # Persistencia local (mis_datos.json + SQLite)
└── references/
    ├── active-ia.md          # Pipeline de corrección automática
    └── skill-v1-browser.md   # Versión anterior (scraping) — consulta histórica
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
