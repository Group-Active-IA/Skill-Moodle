---
name: tup-campus-navigator
description: >-
  Operar el campus Moodle de la TUP (UTN) — Programación I, II y III — por la API
  REST oficial, desde Claude Code. Usá esta skill cuando el usuario mencione "campus
  TUP", "moodle TUP", "tup.sied", "Programación 1/2/3", una "comisión" (C1/C2/C3),
  "informe de seguimiento", "qué me falta corregir", "quién no entregó", "cargar
  nota", "pendientes", "mapeá mis comisiones", "actualizá mis datos", o pida revisar
  entregas / calificaciones / parciales por comisión. También cubre **auditoría de aula
  virtual** ("auditá el aula", "puesta a punto del Moodle", "links rotos", "revisá cómo
  está armada el aula", "planilla de auditoría") y corrección automática con Active-IA
  (importar entregas → corregir con IA → devolver la nota).
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

## El menú — cómo guiar al tutor (hacé esto al activarte)

Cuando la skill se activa **sin un pedido concreto** (el tutor dice "campus", "moodle",
"hola" o abre la skill), NO le preguntes en abstracto qué necesita: **mostrale el menú**
y llevalo de la mano. Un tutor no sabe qué tools existen; el menú es su interfaz.

**Antes del menú, chequeá el estado con `mis_datos` y encadená el gate correcto:**

- **Sin credenciales configuradas** (una tool falla pidiendo credenciales) → primero
  `configurar`: pedile usuario y contraseña de Moodle. No sigas sin esto.
- **Configurado pero `mis_datos` vacío** → primero el mapeo (Paso 0): descubrí y guardá
  sus comisiones. Sin mapeo no hay sobre qué trabajar.
- **Todo listo** → mostrá el menú:

```
📚 Campus TUP — ¿qué querés hacer?

  1. 📊 Analizar mis comisiones   — cómo vienen, qué falta corregir
  2. 👤 Analizar un alumno        — su avance, entregas y notas
  3. 📥 Buscar entregas           — pendientes de una tarea o comisión
  4. ⚙️  Mis datos                — ver mi configuración guardada
  5. 🔄 Remapear mis comisiones   — redescubrir del campus (cambió la cohorte)

Decime el número, o contame con tus palabras qué necesitás.
```

**Qué hace cada opción** (mapeo a tools; el tutor puede pedir por número o por palabras):

1. **Analizar mis comisiones** → `actualizar_tableros` (refrescá primero, avisá que
   tarda) y presentá los pendientes por comisión; ofrecé `armar_informe` (PDF) al final.
2. **Analizar un alumno** → pedí nombre o email, `buscar_alumno`, y mostrá su avance,
   entregas y notas. Si pide corregirle algo, va por el flujo de escritura (con OK).
3. **Buscar entregas** → pedí la tarea/comisión y usá `entregas_tarea`: te da de una el
   padrón completo (quién entregó, quién no y con qué nota) en segundos. `sumario` para el
   conteo oficial. NO corras el snapshot para esto.
4. **Mis datos** → `mis_datos`: mostrale sus cursos, comisiones y tareas mapeadas.
5. **Remapear mis comisiones** → corré el bootstrap de nuevo (`aulas` → elegir materia →
   `descubrir_comisiones` → validar → `guardar_mis_datos`). Útil cuando cambió la cohorte.

Después de resolver una opción, ofrecé volver al menú ("¿algo más? volvemos al menú").
El menú es una ayuda, no una jaula: si el tutor pide algo directo ("qué me falta en la
23"), hacelo sin pasar por el menú.

## Instalación (para el tutor)

Ver `README.md`. En resumen: instalar la skill, instalar las deps del MCP
(`pip install -r mcp/requirements.txt`), conectar el MCP en la config de Claude Code y
configurar las credenciales con la tool `configurar` (el tutor se las dice a Claude; no
hay que setear variables de entorno a mano).

## Paso 0 — Bootstrap (SIEMPRE primero, una sola vez)

**Sin esto, nada funciona.** La skill no puede pedir pendientes ni informes si todavía
no sabe cuáles son las comisiones del tutor. El orden es obligatorio:

1. **Credenciales.** Pedile al tutor su usuario de Moodle (para muchos es el DNI,
   **pero no para todos** — no lo asumas) y su contraseña, y llamá la tool
   `configurar(moodle_user, moodle_pass)`. Esa tool guarda las credenciales en un
   `.env` local (permisos 600, fuera del repo — no se versiona) y **valida el login
   contra el campus** antes de darlo por bueno. El tutor NO setea variables de entorno
   a mano. Si el login falla, la tool lo dice: revisá usuario/contraseña.
2. **Preguntale el nombre y usá `mi_comision`.** Es el atajo: con el nombre del tutor,
   `mi_comision(nombre)` devuelve —de una— su comisión en cada materia (`group_id` real)
   y sus actividades de cursada con `cmid`. El tutor no descubre cursos, ni grupos, ni
   tareas: dice cómo se llama y ya está. Matchea sin acentos ni mayúsculas y acepta el
   apellido suelto; si el nombre da ambiguo devuelve los candidatos —**preguntá cuál es,
   no elijas vos**— y si no está en el reparto, lo dice y caés al mapeo manual del punto
   siguiente. Cada `group_id` se valida contra el campus antes de devolverse, así que un
   catálogo viejo avisa en vez de mentir.

   Un tutor puede tener **varias comisiones** (misma materia o distintas): devolvelas
   todas, no te quedes con la primera.

3. **Si `mi_comision` no lo encuentra — mapeo manual.** Llamá `aulas`: trae las materias de la cohorte
   vigente (Prog I/II/III) ya resueltas a su curso, así el tutor NO tiene que descubrir
   cursos — solo elige su materia. `aulas` valida cada curso contra la cuenta del tutor,
   así que nunca ofrece un aula que no tenga. Si `aulas` avisa que venció o no encuentra
   nada, caé a `descubrir_cursos` en vivo. Con la materia elegida, `descubrir_comisiones`
   trae sus comisiones (group_id REAL) y `listar_tareas` sus actividades.
4. **Validar antes de guardar.** Mostrá el mapeo propuesto y confirmá con el tutor.
   Cada `group_id` que vayas a guardar **tiene que estar en la lista que devolvió
   `descubrir_comisiones`**. Si proponés uno que no está, es un ID inventado:
   rechazalo y volvé a preguntar. (Pasó de verdad: un modelo "mapeó" la comisión 8
   con un group_id que no existía, y TODOS los datos quedaron mal en silencio.)
5. **Guardar** con `guardar_mis_datos`. De ahí en más el tutor no mapea más.

Consultá el estado con `mis_datos`. Si viene vacío, corré el bootstrap antes de nada.

## Las herramientas (qué pedir al MCP)

| Querés… | Tool |
|---|---|
| Ver tu config guardada | `mis_datos` |
| **Saber tu comisión y actividades diciendo tu nombre** | `mi_comision` |
| Elegir materia (aulas pre-cargadas) | `aulas` (fallback: `descubrir_cursos`) |
| Descubrir comisiones / tareas | `descubrir_comisiones`, `listar_tareas` |
| Guardar el mapeo (validado) | `guardar_mis_datos` |
| **Refrescar los datos (snapshot on-demand)** | `actualizar_tableros` |
| Conteo confiable de una tarea | `sumario` |
| **Quiénes entregaron y quiénes deben, con nombre** | `entregas_tarea` |
| Quién entregó y falta corregir | `pendientes_por_corregir` |
| Buscar un alumno (caché del snapshot) | `buscar_alumno` |
| PDF de pendientes | `armar_informe` |
| **Auditar cómo está armada un aula** (read-only) | `auditar_aula` |
| Cargar una nota (con devolución) | `cargar_nota` |
| **Mensajes privados que te faltan contestar** | `mensajes_pendientes` |
| Bandeja de conversaciones · hilo completo | `leer_mensajes` · `leer_conversacion` |
| Mandar un privado a un alumno (pide OK) | `responder_mensaje` |
| **Consultas de foro que te faltan contestar** | `foros_pendientes` |
| Foros del curso · hilos de un foro | `listar_foros` · `leer_foro` |
| Mensajes de una discusión | `leer_discusion` |
| Responder en el foro (pide OK) | `responder_foro` |
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
  para corregir y NO está al día: es el que más debe. `pendientes_por_corregir` solo ve la
  cola de corrección: si devuelve 0, eso NO significa "todos entregaron". Para el padrón
  completo con nombres, `entregas_tarea`.
- **`forum_id` ≠ `cmid`.** `leer_foro` quiere el `forum_id`; el `cmid` es el módulo en
  el aula. Los dos vienen en `listar_foros` y no son intercambiables.
- **Los foros de consultas NO están separados por comisión.** Vienen con `groupid`
  vacío: son de todo el curso (27 comisiones en Prog I). El único group-separado es
  "Avisos de la comisión". Por eso `foros_pendientes` no puede decir "estas consultas
  son de TUS alumnos" en los foros de dudas — las muestra todas, y está bien: cualquier
  tutor puede contestarlas.
- **Filtrar foros por tipo no alcanza.** "Avisos de la comision" está declarado
  `general`, no `news`: filtrar solo por tipo metía cientos de avisos de otros tutores
  como si fueran consultas sin responder (106 falsos positivos en una prueba real). Por
  eso `foros_pendientes` filtra en positivo: solo foros de consultas/dudas, y saltea el
  de "buscar dupla" (que es entre alumnos). Lo salteado se informa en `foros_salteados`,
  nunca en silencio.
- **`leer_mensajes` NO trae el hilo**, solo el último mensaje de cada conversación.
  Antes de contestar un privado, abrí `leer_conversacion`: responder con el último
  mensaje como único contexto lleva a repetir lo ya dicho.
- **"Último mensaje del alumno" ≠ "necesita respuesta".** `mensajes_pendientes` marca
  como pendiente todo hilo cuya última palabra fue del alumno, y ahí caen muchos
  "gracias!" de cierre. Es a propósito: preferimos que sobre y el tutor descarte, antes
  que esconder una consulta real. No lo presentes como "tenés N mensajes sin contestar"
  sin haberlos mirado.
- **Por nombre solo se encuentra a quien ya escribió.** `responder_mensaje` resuelve el
  destinatario entre las conversaciones existentes; para escribirle por primera vez a
  alguien hace falta su email (`buscar_alumno`).
- **Ordenar antes de cortar.** Al toparse la cantidad de discusiones, hay que ordenar
  por fecha primero: cortar en el orden que devuelve el campus escondía pendientes
  viejos sin avisar.

## Cosas que NO hacer

- No mapear a mano ni hardcodear IDs de cohortes viejas.
- No correr snapshots automáticos ni de otros tutores: solo el actual, a pedido.
- No escribir en el campus sin el OK del tutor.
- No dar por bueno un dato que no se pudo verificar en vivo.

## Auditoría de aula virtual (read-only)

`auditar_aula(course_id, materia, evaluador, rol, con_navegador, unidad)` releva cómo está
**armada** un aula (no "quién entregó qué") y escribe un **worksheet `.md`** en `salidas/` —
un BORRADOR basado en evidencia que el evaluador humano completa. Pensada para la "puesta a
punto del Moodle" con la planilla oficial de 6 hojas (Aula Virtual, Unidades, Evaluaciones,
Equipo, Checklist, Desarrollo).

**PREGUNTÁ QUÉ UNIDAD antes de auditar.** Un tutor audita SU unidad, no las 10 — y auditar
todo tarda mucho más (testea todos los links y cuestionarios). Cuando el tutor pida auditar,
ofrecele: "¿qué unidad? (1-10)" o "todo el aula". Con la respuesta corré `auditar_aula` con
`unidad=N` (o sin `unidad` para el aula entera). Si el número no existe, la tool devuelve
`unidades_disponibles` para reintentar.

**La regla que la ordena (la misma de la skill): un agente verifica presencia / ausencia
/ consistencia, NUNCA calidad.** Puntaje autolimitado: `0` = ausente (verificado), `3` =
presente, **vacío** = no verificable sin leer contenido → "sin dato", jamás se infiere.
La calidad y los puntajes finos los pone la persona que firma.

Cómo trabaja:

- **Matriz de unidades** (el 80% del valor): agrupa las secciones por cabecera numerada
  (`1- …`, `10- …`) con sus hijas (Actividades, Práctica, Microteaching, Autoevaluación,
  Encuesta) y marca presencia de los 9 componentes. Los nombres de unidad salen del aula,
  no se hardcodean: vale para Prog I/II/III aunque cambien los temas.
- **Links**: testea los externos (404 real = roto; otro campus; href con espacio). Los del
  propio campus no se piden (su existencia la confirma la API). **Las apps de Google
  (NotebookLM/Colab/YouTube) NO se juzgan por un GET** —siempre parece login o rate-limit—:
  se marcan "requiere navegador" y las resuelve el pase navegador.
- **Hallazgos**: componente faltante sistemático, hueco en un patrón (9 de 10), instancia
  extraordinaria con `visible=1` (posible fuga de examen — se enmarca como *verificar*).

**Pase navegador (`con_navegador=True`, opcional):** para lo que la API no ve. Loguea por
navegador (Playwright, sesión persistida) y cuenta las **preguntas** de cada cuestionario
abriendo su edición (mini=4 / autoeval=10 según la planilla → hallazgo si difiere) y
clasifica las **apps Google** en *abren* (Colab público) vs *piden cuenta* (NotebookLM).
Requiere `pip install playwright && playwright install chromium`; si falta, se saltea con
aviso y la auditoría por API igual corre. `mod/quiz/edit.php` necesita rol docente sobre
el quiz (el tutor de la TUP lo tiene). Tarda: abre una página por cuestionario.

Guardrails (no negociables):

1. **Read-only sobre Moodle.** No corrige, no publica, no mensajea. Solo escribe el `.md`.
2. **La hoja EQUIPO se deja VACÍA.** Un agente no evalúa el desempeño de personas.
3. **Describe el hecho y su impacto, no juzga a la persona.** "El foro U6 tiene 12
   consultas sin responder" ✅, no "el tutor no atiende" ❌.
4. **El worksheet es un BORRADOR, no un veredicto.** Lo dice el propio archivo.
5. **Lo no verificado va a "a revisar", nunca a "Sí".** Un link no testeado no funciona.

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

El detalle de estados y modo híbrido está en **`references/active-ia.md`**.
