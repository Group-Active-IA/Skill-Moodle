# Tareas del equipo — ClickUp

Guía para un agente que tiene que operar esta integración. Todo lo de acá está
verificado contra el workspace real ("Tutorías TUPAD") en vivo (2026-08-27); donde
algo no se pudo confirmar, se dice.

## Qué es

ClickUp es un servicio **externo y aparte de Moodle**, ya usado activamente por el
equipo docente para repartirse trabajo puntual que no es corrección de TPs: armar un
parcial, revisar un integrador, cargar links, auditar preguntas. No hay wrapper Python
para esto: la skill llama directo las tools del **MCP oficial de ClickUp**
(`mcp__clickup__*`), agente-orquestadas igual que muchos flujos de lectura de Moodle.

El workspace ya tenía, antes de esta integración, un folder por materia (PROG I/II/III/IV)
con una lista **"Tareas"** en uso real: 49 tareas con responsable, estado y fecha en la
muestra relevada. Esta integración es aditiva sobre eso — no crea un tablero nuevo.

## Cómo se conecta

El servidor MCP de ClickUp (`https://mcp.clickup.com/mcp`) es un MCP HTTP con login
OAuth — nada de tokens personales para copiar y pegar.

**Instalación, una vez por máquina:**

```
claude mcp add --transport http clickup https://mcp.clickup.com/mcp -s user
```

`-s user` (no `-s local`) importa: sin él, el servidor queda pegado SOLO a la carpeta
desde la que se corrió el comando (scope "local" — privado a ese proyecto), y un tutor
casi nunca va a abrir Claude Code parado justo ahí. Mismo criterio que ya usa esta
skill consigo misma en `install.sh` (`claude mcp add moodle-tutor -s user`).

**Login — el paso que se traba:**

```
claude mcp login clickup
```

**Tiene que correr en una terminal REAL y aparte** (PowerShell o cmd abiertos por fuera
de Claude Code), nunca con el prefijo `!` dentro del chat. Verificado dos veces: con
`!` falla con `stdin isn't a terminal, so authentication can't be completed here` —
ese prefijo ejecuta el comando en la sesión, pero no le da una TTY real, y el flujo
OAuth necesita una de verdad para esperar el callback. Abrí PowerShell desde el menú
de Windows, corré el comando ahí, logueate en el navegador que se abre, y volvé a
Claude Code (sesión nueva si hace falta).

**Confirmar:** `claude mcp list` tiene que mostrar `clickup ... ✔ Connected`. Desde
la skill, el self-check liviano es `clickup_get_workspace_hierarchy(max_depth="0")` —
si responde, hay conexión.

## El catálogo (`mcp/clickup.json`)

Mapea materia → folder/list, para no redescubrir IDs cada vez. Mismo espíritu que
`comisiones.json`/`aulas.json`: bundled, versionado, y **validado en vivo antes de
confiar** (no asumir que sigue igual para siempre).

```json
{
  "workspace_id": "90171440963",
  "space": {"nombre": "Tutorías TUPAD", "space_id": "90176760252"},
  "estados": {"abierto": "to do", "en_curso": "in progress", "hecho": "complete"},
  "materias": [
    {"materia": "Programación I",   "folder_id": "901710309789", "tareas_list_id": "901715787974"},
    {"materia": "Programación II",  "folder_id": "901710324456", "tareas_list_id": "901715806386"},
    {"materia": "Programación III", "folder_id": "901710324686", "tareas_list_id": "901715806698"},
    {"materia": "Programación IV",  "folder_id": "901710325036", "tareas_list_id": "901715807181"}
  ]
}
```

**Qué NO está mapeado:** `"Programación I - Marzo 2026"` (cohorte legacy en
`comisiones.json`) no tiene folder propio en ClickUp — usa la lista de "Programación I"
hasta que se confirme si necesita la suya.

**Ojo con el nombre repetido.** Cada materia tiene DOS folders en ClickUp con el mismo
nombre visible: uno con el triage "Equipo docente"/"Cronograma"/**"Tareas"** (el que
usa esta integración) y otro que solo tiene "Material PROG N" (materiales de cursada,
no trabajo asignable). Nunca confundir el `tareas_list_id` del catálogo con el de
"Material" — si en algún momento hay que resolver a mano con
`clickup_get_workspace_hierarchy`, filtrar por el nombre de la LISTA ("Tareas"), no
solo por el del folder.

## Semántica exacta de cada tool usada

- **`clickup_filter_tasks(list_ids, assignees, statuses, include_closed, workspace_id,
  order_by, page)`** — filtro combinado, AND entre filtros y OR dentro de cada uno.
  `assignees` son **IDs numéricos de ClickUp**, no nombres — resolver antes. **Pagina de
  a 100**: si la respuesta trae `has_more=true`, hay que repetir con `page=next_page`
  hasta que `has_more=false` para no perder tareas. El board actual (49 tareas en total)
  nunca ejercita esto en la práctica — implementar el loop igual, por spec, no porque
  se haya podido probar contra datos reales que lo disparen.
- **`clickup_create_task(list_id, name, markdown_description, due_date, priority,
  assignees, status, tags, workspace_id, ...)`** — `due_date` en `YYYY-MM-DD`,
  `priority` uno de `urgent`/`high`/`normal`/`low` (o `null`). `assignees` acepta IDs,
  emails o `"me"` y los resuelve solo, pero para evitar ambigüedad esta skill SIEMPRE
  resuelve antes con `clickup_find_member_by_name` y muestra el resultado.
- **`clickup_update_task(task_id, status, assignees, due_date, priority, ...)`** —
  `status` tiene que ser el nombre EXACTO configurado en esa lista (case-sensitive:
  `"to do"`, `"in progress"`, `"complete"`). Si lo rechaza, no reintentes con un nombre
  inventado: confirmá con `clickup_get_list(list_id)` cuáles son los estados reales.
- **`clickup_find_member_by_name(name_or_email)`** — **NO hace matching difuso.**
  Verificado en vivo: `"Neyén Bianchi Medina"` (nombre de cátedra completo) y
  `"Juan Sarmiento"` devolvieron `{"member": null}` — solo funcionó con el nombre EXACTO
  de ClickUp (`"Neyén Bianchi"`) o el email exacto. Como el nombre de cátedra casi nunca
  es idéntico al de ClickUp (apellidos compuestos, sin segundo nombre, etc.), **el flujo
  correcto es de dos pasos, no uno**:
  1. Probá primero con el nombre completo de `comisiones.json` (es gratis, a veces
     matchea tal cual).
  2. Si devuelve `null`, llamá `clickup_get_workspace_members()` UNA vez (trae la lista
     completa, ~30 miembros) y buscá vos vos mismo por coincidencia parcial —
     apellido, sin acentos, ignorando mayúsculas — contra los `name`/`username`/`email`
     de la respuesta. Mostrale al tutor/profesor el candidato (o candidatos, si hay más
     de uno) para que **confirme**, nunca asumas el primero que matchea.
- **`clickup_resolve_assignees(assignees[])`** — para lotes de nombres/emails/`"me"`.
- **`clickup_get_list(list_id | list_name)`** — para validar el catálogo contra la
  lista real (nombre, estados) antes de confiar ciegamente en `clickup.json`.
- **`clickup_get_workspace_hierarchy(max_depth)`** — el self-check liviano de conexión
  (`max_depth="0"` alcanza: solo hace falta saber si responde).

## Gotchas

- **Nombre de cátedra ≠ nombre de ClickUp, y la resolución NO es difusa.** Caso real
  verificado en vivo: `comisiones.json` tiene `"Neyén Bianchi Medina"`, el miembro de
  ClickUp es `"Neyén Bianchi"` (mismo `id`, string distinto) — `clickup_find_member_by_name("Neyén
  Bianchi Medina")` devuelve `null`, solo el nombre EXACTO de ClickUp encuentra algo.
  Nunca uses el resultado `null` como "no está en ClickUp": siempre hay que caer a
  `clickup_get_workspace_members()` y matchear a mano antes de darlo por ausente.
- **"Material" vs "Tareas".** Ver arriba — dos folders con el mismo nombre de materia,
  listas distintas.
- **Multi-workspace.** Un tutor puede tener sus propios workspaces personales de
  ClickUp aparte de "Tutorías TUPAD". Pasar siempre `workspace_id="90171440963"`
  explícito en las llamadas que lo aceptan, para no resolver contra el equivocado.
- **Estados case-sensitive** y pueden desincronizarse si alguien edita una lista a
  mano — validar con `clickup_get_list` ante cualquier duda, no hardcodear ciego.

## Doctrina anti-ranking (calcada de `panorama_comisiones`)

La vista "carga de tareas por tutor" son HECHOS de ruteo, no una evaluación de
personas: nombrar a alguien con su cantidad de tareas abiertas sirve para repartir
mejor el trabajo nuevo, no para compararlo con sus colegas.

- **NO** armar ranking, podio ni puntaje por tutor. Ordenar por cantidad de tareas
  abiertas o por vencidas para saber por dónde arrancar está bien; presentarlo como
  "tabla de posiciones" no.
- Un número alto puede ser una semana de parcial, no falta de compromiso. Ofrecé el
  dato, no el veredicto.

## Flujos completos

### Tutor — ver mis tareas (primera vez, sin `user_id` guardado)

1. `mis_datos()` → confirmar que el Paso 0 de Moodle ya corrió.
2. Self-check de conexión ClickUp (`clickup_get_workspace_hierarchy(max_depth="0")`).
   Si falla, cortar con las instrucciones de "Cómo se conecta" de arriba.
3. `clickup_find_member_by_name(name_or_email=<nombre de mis_datos.tutor.nombre>)`. Si
   devuelve `null` (lo más probable — ver Gotchas: no matchea nombres de cátedra tal
   cual), `clickup_get_workspace_members()` y buscá vos la coincidencia parcial.
4. Mostrarle al tutor el match ("¿sos vos? Neyén Bianchi, neyen@...") y **confirmar
   explícitamente** — nunca asumir por el nombre solo, y si hay más de un candidato
   parecido, listalos todos y que el tutor elija.
5. `guardar_clickup_id(clickup_user_id=<id>, nombre_clickup=<name>, email=<email>)`.
6. Seguir al flujo de abajo.

### Tutor — ver mis tareas (con `user_id` ya guardado)

1. `mis_datos()` → tomar `datos.clickup.user_id` y las materias de `datos.cursos[*]`.
2. Cruzar cada materia contra `mcp/clickup.json.materias[*].tareas_list_id` (un tutor
   con varias materias junta todos sus `list_ids` en una sola consulta).
3. `clickup_filter_tasks(list_ids=[...], assignees=[user_id], statuses=["to do","in
   progress"], include_closed=false, workspace_id="90171440963")`.
4. Presentar agrupado por materia, con vencimiento y prioridad.

### Tutor — marcar una tarea como hecha

1. Identificar la tarea (por nombre, o de la lista ya mostrada con su `task_id`).
2. Mostrar exactamente qué se va a cerrar (nombre, lista) y **pedir OK**.
3. `clickup_update_task(task_id=..., status="complete")`.
4. Confirmar y ofrecer volver al menú.

### Profesor — crear y asignar una tarea

1. Materia → `mcp/clickup.json.materias[*].tareas_list_id`.
2. Nombre del tutor → cruzar contra `comisiones.json` → `clickup_find_member_by_name`
   (probablemente `null`: ver Gotchas) → si no matcheó, `clickup_get_workspace_members`
   y resolver a mano. Si hay más de un candidato parecido, listarlos y preguntar.
3. Reunir: título, descripción, `due_date`, `priority`, `assignees=[id]`.
4. **Preview completo + OK explícito** — misma regla que `cargar_nota`.
5. `clickup_create_task(list_id=..., name=..., markdown_description=..., due_date=...,
   priority=..., assignees=[id], workspace_id="90171440963")`.
6. Devolver el link/ID de la tarea creada.

### Profesor — carga de tareas por tutor (read-only)

1. `clickup_filter_tasks(list_ids=<las materias pedidas>, statuses=["to do","in
   progress"], include_closed=false, workspace_id="90171440963")`.
2. Agrupar client-side por `assignees`.
3. Presentar como dato de ruteo ("Fulano tiene 4 tareas abiertas, 1 vencida"), siguiendo
   la doctrina anti-ranking de arriba.
