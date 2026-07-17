# Active-IA — corrección automática (API REST)

Pipeline opcional: baja el trabajo del alumno de Moodle → lo sube a Active-IA → Active-IA
lo corrige con IA (Gemini, server-side) contra la rúbrica → devuelve la nota y un PDF de
devolución. En la skill v2 esto va **por API REST con JWT** (las tools `activeia_*` del
MCP), no por navegador.

## Requisitos

- Cuenta de Active-IA del tutor: `ACTIVEIA_USER`, `ACTIVEIA_PASS` (las carga `configurar`,
  igual que las de Moodle). `ACTIVEIA_URL` por default apunta a la API de Active-IA.
- Gemini corre **del lado de Active-IA**: el tutor NO pasa una API key de Gemini.

## Flujo

1. **`activeia_pendientes()`** — devuelve el mapa Moodle ↔ Active-IA del tutor logueado:
   sus materias → unidades → comisiones, con el `rubrica_id` inferido cuando se puede.
   Cada tutor ve SOLO sus comisiones; **nunca asumas IDs de otra cuenta**, salen de acá.

2. **`activeia_resolver(assign_id, group_id)`** — para una tarea concreta de Moodle,
   resuelve el `comision_id` y el `rubrica_id` de Active-IA que le corresponden. Si no
   puede inferir la rúbrica, devuelve las opciones para que el tutor elija.

3. **`corregir_con_active_ia(assign_id, email, comision_id, rubrica_id, confirmado=...)`**
   — el flujo completo: baja el `.zip` del alumno de Moodle (por REST), lo sube a
   Active-IA, dispara la corrección, espera el resultado, y **descarga el PDF de
   devolución** a `$MOODLE_SKILL_HOME/salidas/devolucion_{correccion_id}.pdf`. Devuelve
   `{ok, nota, devolucion_pdf_local, correccion_id, estado}`.

## Es una escritura: pedí OK

`corregir_con_active_ia` dispara la corrección con IA y puede escribir la nota en Moodle.
Con `confirmado=false` devuelve un **preview** de lo que va a hacer, sin ejecutar nada.
Mostráselo al tutor y volvé a llamar con `confirmado=true` solo tras su OK explícito.

## Modo híbrido (criterio recomendado)

Al corregir en lote, subí automáticamente lo que quedó **claramente aprobado** y **frená
para revisión humana** los casos borde (nota límite, corrección con baja confianza, o
entregas que Active-IA marcó con error). El tutor revisa esos a mano antes de cargar la
nota. La idea: automatizar lo seguro, no lo dudoso.

## Estados de una entrega en Active-IA

Una entrega pasa por estados (pendiente → en corrección → corregida / con error). El
poll de `corregir_con_active_ia` espera hasta que termina o corta por timeout. Si Active-IA
devuelve error de entrega (archivo ilegible, rúbrica que no matchea), la tool lo reporta
en vez de dar una nota inventada.

## Gotchas

- **Cada tutor ve sus propias comisiones y rúbricas.** Los `comision_id`/`rubrica_id` no
  son globales: se descubren por la cuenta del tutor logueado (`activeia_pendientes`).
- **El conteo de alumnos puede no coincidir con el campus**: Active-IA acumula entregas de
  cohortes previas. Filtrá siempre por comisión + rúbrica + estado.
- **Trabajos que no corrige la IA** (ej. repos de Git): no todos pasan por Active-IA;
  algunos se revisan a mano. No fuerces la corrección automática donde no aplica.
