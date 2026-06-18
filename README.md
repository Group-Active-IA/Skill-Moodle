# TUP Campus Navigator — Programación I

Skill de [Claude Code](https://claude.com/claude-code) para tutores de **Programación I** de la Tecnicatura Universitaria en Programación (UTN), sobre el campus Moodle `https://tup.sied.utn.edu.ar` y el corrector automático **Active-IA**.

Automatiza tareas de tutoría que de otra forma son repetitivas y propensas a error:

- **Informes de seguimiento** por comisión: quién no entregó, deudas, entregas atrasadas, último acceso, alumnos en peligro.
- **Mapeo de participantes** y entregas por grupo (comisión).
- **Pipeline de corrección automática (Active-IA)**: importar entregas de Moodle → corregir con IA (Gemini) → devolver la nota a Moodle, con un modo **híbrido** que sube en lote lo aprobado y frena los casos borde para revisión humana.

> Pensada para Programación I, pero los patrones de Moodle (URLs, snippets de extracción) sirven de base para otras materias del mismo campus.

## Sin datos personales

Esta skill **no trae credenciales ni IDs hardcodeados de ningún tutor**. Al arrancar ejecuta un **Paso 0 — Bootstrap** que:

1. Pregunta tu **nombre** y tus **credenciales** (Moodle = DNI + contraseña; Active-IA = tu cuenta propia + tu API key de Gemini).
2. **Descubre en vivo** desde el campus el course ID, tus comisiones (grupos) y los IDs de TPs/parciales — no asume los del documento.
3. Verifica contra la caché de referencia; si cambió la cohorte, usa los valores reales.

Resultado: la skill queda personalizada para vos sin editar un solo archivo. Tu contraseña **nunca** se escribe a disco ni a memoria — solo se usa en el form de login.

## Requisitos

- **Claude Code** instalado.
- **Playwright MCP** configurado (la skill maneja el browser vía `browser_*`).
- Cuenta de tutor en el campus Moodle de la TUP con permisos sobre al menos una comisión de Programación I.
- *(Opcional, para corrección automática)* Cuenta en Active-IA + API key de Google Gemini propia.

## Instalación

Cloná el repo dentro de tu carpeta de skills de Claude Code:

```bash
git clone <URL-DEL-REPO> ~/.claude/skills/tup-campus-navigator
```

Listo. La skill se descubre sola. Para usarla, en una sesión de Claude Code decí algo como:

- "Entrá al campus TUP y hacé un informe de seguimiento de mi comisión"
- "Corregí los TPs pendientes con Active-IA"
- "¿Quién no entregó el TP7?"

Claude carga la skill, te pide identidad + credenciales, y opera.

## Privacidad y seguridad

- No commitees credenciales ni datos de alumnos. Los datos extraídos del campus se guardan localmente en `~/.playwright-mcp/` (fuera del repo).
- El pipeline de Active-IA tiene **frenos humanos obligatorios**: no sube desaprobados, notas al límite, ni pisa notas manuales sin tu confirmación explícita.

## Licencia

MIT (ver `LICENSE`).
