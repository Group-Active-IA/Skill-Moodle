---
name: tup-campus-navigator
description: >-
  Operar el campus Moodle de la TUP (UTN) — Programación I, II y III — por la API
  REST oficial, desde Claude Code. Usá esta skill cuando el usuario mencione "campus
  TUP", "moodle TUP", "tup.sied", "Programación 1/2/3", una "comisión" (C1/C2/C3),
  "informe de seguimiento", "qué me falta corregir", "quién no entregó", "cargar
  nota", "pendientes", "mapeá mis comisiones", "actualizá mis datos", o pida revisar
  entregas / calificaciones / parciales por comisión. También cubre corrección
  automática con Active-IA (importar entregas → corregir con IA → devolver la nota).
  La skill habla con Moodle por peticiones REST (token moodle_mobile_app), NO por
  navegador. NO la uses para campus que no sean el de la TUP, ni para tareas de
  Moodle como administrador (crear cursos, matricular): es para el trabajo de un
  tutor sobre SUS comisiones.
license: Apache-2.0
---

# TUP Campus Navigator — API REST

Skill para que un **tutor** de la Tecnicatura Universitaria en Programación (UTN)
opere su trabajo en el campus Moodle `https://tup.sied.utn.edu.ar` desde Claude
Code: ver qué le falta corregir, generar informes, cargar notas y (opcional)
corregir con Active-IA — todo por la **API REST oficial** de Moodle, sin navegador.

**Cómo funciona por dentro:** la skill trae un MCP server liviano (`mcp/`) que el
tutor corre local con sus credenciales. Ese MCP saca un token del campus
(`login/token.php?service=moodle_mobile_app`, el mismo que usa la app móvil, sin
admin) y hace peticiones a `webservice/rest/server.php`. Devuelve JSON estructurado,
no HTML: por eso no se rompe cuando el campus cambia de diseño.

> **Regla que ordena todo: verificar en vivo, nunca inventar.** Cada ID (curso,
> comisión, tarea) se descubre del campus y se valida contra lo que el campus
> devuelve. Si un dato no se pudo confirmar, se dice "no pude", nunca se rellena con
> algo plausible.

## Cuándo activarla

- "Entrá al campus" / "abrí Moodle TUP" / "tup.sied"
- "Mapeá mis comisiones" / "actualizá mis datos" / "configurá mi cohorte"
- "¿Qué me falta corregir?" / "pendientes" / "quién no entregó" / "deudas"
- "Informe de la comisión X" / "PDF de pendientes"
- "Cargá 8 al TP de tal alumno" (escritura → pide OK antes)
- "Corregí los TPs con Active-IA" / "importá entregas" / "devolución automática"

## Instalación (para el tutor)

Ver `README.md`. En resumen: instalar la skill, instalar las deps del MCP
(`pip install -r mcp/requirements.txt`), conectar el MCP en la config de Claude Code
y setear las credenciales (`MOODLE_URL`, `MOODLE_USER`, `MOODLE_PASS`) como env vars.

## Paso 0 — Bootstrap (SIEMPRE primero, una sola vez)

**Sin esto, nada funciona.** La skill no puede pedir pendientes ni informes si todavía
no sabe cuáles son las comisiones del tutor. El orden es obligatorio:

1. **Credenciales.** Pedile al tutor su usuario de Moodle (para muchos es el DNI,
   **pero no para todos** — no lo asumas) y su contraseña, y llamá la tool
   `configurar(moodle_user, moodle_pass)`. Esa tool guarda las credenciales en un
   `.env` local (permisos 600, fuera del repo — no se versiona) y **valida el login
   contra el campus** antes de darlo por bueno. El tutor NO setea variables de entorno
   a mano. Si el login falla, la tool lo dice: revisá usuario/contraseña.
2. **Descubrir en vivo.** Con `descubrir_cursos` y `descubrir_comisiones` traé del
   campus los cursos del tutor, sus comisiones (con el `group_id` REAL) y las tareas
   (`listar_tareas`). **No asumas IDs de ninguna tabla de referencia**: cambian por
   cohorte.
3. **Validar antes de guardar.** Mostrá el mapeo propuesto y confirmá con el tutor.
   Cada `group_id` que vayas a guardar **tiene que estar en la lista que devolvió
   `descubrir_comisiones`**. Si proponés uno que no está, es un ID inventado:
   rechazalo y volvé a preguntar. (Pasó de verdad: un modelo "mapeó" la comisión 8
   con un group_id que no existía, y TODOS los datos quedaron mal en silencio.)
4. **Guardar** con `guardar_mis_datos`. De ahí en más el tutor no mapea más.

Consultá el estado con `mis_datos`. Si viene vacío, corré el bootstrap antes de nada.

## Las herramientas (qué pedir al MCP)

| Querés… | Tool |
|---|---|
| Ver tu config guardada | `mis_datos` |
| Descubrir cursos / comisiones / tareas | `descubrir_cursos`, `descubrir_comisiones`, `listar_tareas` |
| Guardar el mapeo (validado) | `guardar_mis_datos` |
| **Refrescar los datos (snapshot on-demand)** | `actualizar_tableros` |
| Conteo confiable de una tarea | `sumario` |
| Quién entregó y falta corregir | `pendientes_por_corregir` |
| Padrón de una comisión · buscar alumno | `padron` · `buscar_alumno` |
| PDF de pendientes | `armar_informe` |
| Cargar una nota (con devolución) | `cargar_nota` |
| Ver el mapa Moodle ↔ Active-IA | `activeia_pendientes` |
| Resolver comisión/rúbrica de Active-IA | `activeia_resolver` |
| **Corregir con Active-IA + PDF de devolución** | `corregir_con_active_ia` |

## Reglas de oro (no negociables)

1. **Mapear primero.** Sin `mis_datos` no hay sobre qué trabajar (Paso 0).
2. **Nunca inventar un ID.** Todo group_id/assign_id sale del campus y se valida
   contra lo que el campus devolvió. Un ID que no está en la lista real no se usa.
3. **Nunca reportar éxito si una tool falló.** Si una tool devuelve `omitido`,
   `error` o vacío, **decilo** — no digas "listo, 0 pendientes, al día". Un "0"
   puede significar "no hay pendientes" o "no se relevó nada": son OPUESTOS y hay que
   distinguirlos. (Pasó: se reportó "all clear" cuando el snapshot se había omitido.)
4. **Multi-curso.** Un tutor puede tener Prog I, II y III a la vez. Nunca fijes un
   curso: operá sobre TODOS los de `mis_datos`.
5. **Escrituras con OK explícito.** `cargar_nota`, `responder_foro`,
   `responder_mensaje` tocan el campus de alumnos reales. Mostrá exactamente qué vas a
   escribir y esperá el OK del tutor ANTES de ejecutar.
6. **Snapshot solo a pedido.** `actualizar_tableros` corre cuando el tutor lo pide, no
   solo. Avisá que puede tardar.
7. **Conteo confiable = `sumario`.** No cuentes filas a mano; el sumario oficial es la
   fuente.

## Gotchas de la API (ya resueltos en el código, no re-tropezar)

- **instanceid ≠ cmid.** Los web services de assign usan el **instanceid** de la
  tarea, no el cmid de la URL. El cliente ya mapea cmid→instanceid.
- **`mod_assign_save_grade` exige la devolución** (plugindata con contenido) o tira un
  error de base de datos engañoso. `cargar_nota` ya la manda.
- **Escala invertida.** En Aprobado/Desaprobado los valores están invertidos
  (Aprobado=1, Desaprobado=2). NO hardcodear: leer la opción por texto.
- **"Pendiente de corregir" ≠ "deuda del alumno".** El que no entregó nada tiene 0
  para corregir y NO está al día: es el que más debe.

## Cosas que NO hacer

- No mapear a mano ni hardcodear IDs de cohortes viejas.
- No correr snapshots automáticos ni de otros tutores: solo el actual, a pedido.
- No escribir en el campus sin el OK del tutor.
- No dar por bueno un dato que no se pudo verificar en vivo.

## Corrección automática con Active-IA

Pipeline opcional, por API REST (JWT). Requiere cuenta de Active-IA (env vars
`ACTIVEIA_URL`, `ACTIVEIA_USER`, `ACTIVEIA_PASS`). Gemini corre server-side en
Active-IA — el tutor NO pasa una API key de Gemini.

**Flujo** (para "corregí el TP X del alumno Y con Active-IA"):

1. `activeia_pendientes` / `activeia_resolver(assign_id, group_id)` → obtené el
   `comision_id` y `rubrica_id` de Active-IA para esa tarea.
2. `corregir_con_active_ia(assign_id, email, comision_id, rubrica_id, confirmado=...)`:
   baja el trabajo del alumno de Moodle (REST), lo sube a Active-IA, que lo corrige
   con Gemini, y **descarga el PDF de devolución** a `$MOODLE_SKILL_HOME/salidas/`.
   Devuelve `{ok, nota, devolucion_pdf_local, correccion_id, estado}`.

**Es una ESCRITURA** (dispara la corrección y puede cargar la nota en Moodle): con
`confirmado=false` devuelve un preview de lo que va a hacer SIN ejecutar. Mostráselo
al tutor y volvé a llamar con `confirmado=true` solo tras su OK explícito.

El detalle de estados y modo híbrido está en **`references/active-ia.md`**; los
snippets de la versión browser anterior, en `references/skill-v1-browser.md`.
