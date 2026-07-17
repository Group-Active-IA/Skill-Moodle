# Active-IA — pipeline de corrección automática

> Extraído de la skill v1 (flujo con browser). El pipeline se apoya en Active-IA
> (importar entregas → corregir con IA/Gemini → devolver nota). Migrar a API/scripts
> donde se pueda; mantener el criterio del modo híbrido (sube en lote lo aprobado,
> frena los borde para revisión humana).

## Active-IA — Pipeline de corrección automática

Active-IA es un corrector automático. ⚠️ **Migró de dominio (verificado 2026-06-30):** `active-ia-correcion-automatica.vercel.app` ahora **redirige a `app.active-ia.com`** (la app), con API en `api.active-ia.com` y los PDF de devolución hosteados en `api.active-ia.com/api/v1/public/devoluciones/<jwt>`. El login por la URL vieja de Vercel sigue funcionando (redirige). **Cada tutor usa su PROPIA cuenta de Active-IA** (usuario + contraseña pedidos en el Paso 0.1, y API key de Gemini propia configurada en `/perfil` — ver abajo). **Flujo (verificado en vivo 2026-06-04)** — casi todo ocurre dentro de Active-IA, que se conecta a Moodle por sí mismo:

> **Importar de Moodle → Corregir con IA → Devolver la nota a Moodle.**
> Ya NO hace falta bajar el `.zip` del campus a mano, ni subir el PDF a Drive, ni pegar el feedback en Moodle a mano. El PDF de devolución se hostea en el backend de Active-IA (`active-ia-backend.3xzl86.easypanel.host`) y se linkea solo en el comentario de Moodle.

### ⚙️ Prerequisito de cuenta (one-time, en `/perfil`)

El flujo nuevo NO funciona sin esto:

1. **API Key de Google Gemini (propia del tutor)** — obligatoria para que "Corregir" funcione. Cada tutor carga la suya en `/perfil`. Si "Corregir" tira error, revisar acá. La opción **"API key con facturación (paga)"** habilita el botón **"Corregir todo"** (corrección masiva).
2. **Credenciales Moodle del tutor** (Host / Usuario / Contraseña, cifradas AES-256) — lo que permite a Active-IA **importar entregas en vivo de Moodle** y **devolver la nota a Moodle**. Son las MISMAS credenciales Moodle del Paso 0.1. Si `/pendientes` no trae conteos o "Importar"/"Subir corrección a Moodle" fallan, revisar acá.

### Credenciales y URLs base

- Login: `https://active-ia-correcion-automatica.vercel.app/login` — usuario y password **propios del tutor** (pedidos en el Paso 0.1; nunca hardcodear ni guardar la contraseña)
- Dashboard: `/dashboard`
- **Pendientes** (`/pendientes`): lo que falta corregir, agregado por TP × Comisión, con conteos en vivo de Moodle. Tiene "Importar".
- **Entregas** (`/entregas?comision_id=<X>&rubrica_id=<Y>[&estado=<E>]`): tabla de entregas + menú ⋮ de acciones.
- **Por entregar** (`/por-entregar`): **vista clave del flujo nuevo** — cola de correcciones ya hechas que todavía NO se subieron a Moodle. Botón **"Entregar todo"**.
- Perfil: `/perfil` — API key Gemini + credenciales Moodle.
- Comisiones: `/comisiones`

### Viewport

Setear **1400×900** (`browser_resize`) para ver todas las columnas de la tabla de entregas.

### Mapeo Active-IA ↔ Campus — EJEMPLO (cohorte Marzo 2026)

⚠️ Active-IA usa nomenclatura propia y **cada tutor ve SUS comisiones**. NO asumas estos IDs: el `comision_id` real sale del combo de `/comisiones` o de los links de `/pendientes` de la cuenta del tutor logueado. Ejemplo de cómo se ve el mapeo (cuenta concreta, cohorte Marzo 2026):

| Active-IA | Campus | Notas |
|---|---|---|
| **COMI-23** (comision_id=65) | **M26 C1-23** (group=4292) | — |
| **COMI-25** (comision_id=66) | **M26 C1-25** (group=4299) | — |

Pueden aparecer comisiones de prueba o de otras cohortes en el combo — **operar solo sobre las comisiones de Programación I que correspondan al tutor** (las descubiertas en el Paso 0.3).

### Mapeo de rúbricas (`rubrica_id` por TP, compartido COMI-23 / COMI-25)

⚠️ **No los adivines: los `rubrica_id` vienen en los links "Ver entregas" de `/pendientes`.** El combo de Rúbrica en `/entregas` es **async** — esperá 2-3 s o lo agarrás deshabilitado. IDs confirmados 2026-06-04:

| TP Active-IA | Nombre | Campus equiv. | rubrica_id |
|---|---|---|---:|
| TP 1 | Práctico 1: Estructuras secuenciales | TP1 (11169) | (leer de /pendientes) |
| TP 2 | Práctico 2: Estructuras condicionales | TP2 (11214) | (leer de /pendientes) |
| **TP 3** | TP Integrador: Repetitivas, Condicionales y Secuenciales | TP3 (11237) | **67** |
| TP 4 | Práctico 4: Estructuras repetitivas | — (campus TP4 es Git) | (leer de /pendientes) |
| **TP 5** | Práctico 5: Listas | TP5 (11261) | **69** |
| **TP 6** | Práctico 6: Funciones en Python | TP6 (11286) | **70** |
| **TP 7** | Práctico 7: Estructuras de datos complejas | TP7 (11312) | **74** |
| **TP 8** | Práctico 8: Manejo de errores | TP8 (13761) | **71** |
| **TP 9** | Práctico 9: Manejo de Archivos | TP9 (11337) | **72** |
| **TP 10** | Práctico 10: Recursividad | TP10 (11355) | **73** |
| PARCIAL_1 | Parcial 1 - Inventario de Ferretería | 11383 | **79** |
| RECUPERATORIO_1 | Recuperatorio Parcial 1 | 11381 | **84** |
| PARCIAL_2 | Parcial 2 - Sistema de Control de Inventario | **17442** (group 4299) | **98** |

⚠️ **Campus TP4 (Trabajo Colaborativo Git) NO existe en Active-IA** — no se evalúa con corrector automático.

### Estados de una entrega en Active-IA

- **(no aparece en la tabla)** → todavía en Moodle, no importada. En `/pendientes` cuenta como **"X espera"**. Hay que **Importar**.
- **⚠️ Subida** → importada/cargada, sin corregir. Falta disparar "Corregir".
- **✅ Corregida** → tiene nota numérica (0-100). Lista para devolver a Moodle.
- **Con errores / Archivada** → estados auxiliares.

### Workflow nuevo — Procesar TPs pendientes (modo HÍBRIDO)

> 🚦 **Regla híbrida (decisión del tutor):** lo masivo y claro va automático; los casos borde llevan freno humano.
> - **Automático**: TPs (no parciales) con veredicto **Aprobado** → se devuelven en lote con "Entregar todo".
> - **Freno humano (confirmar con el tutor antes de enviar)**: cualquier **Desaprobado**, notas al límite del corte Aprobado/Desaprobado, y todo lo que Active-IA marque como **"Requiere comentario"**.
> - Nunca tocar parciales/recuperatorios (ver abajo).

#### Paso 0 — Login y relevamiento

1. `browser_resize` 1400×900. Login en `/login` (usuario / password del tutor, del Paso 0.1).
2. Ir a `/pendientes`, esperar a que desaparezca el spinner **"Cargando pendientes…"** (`browser_wait_for textGone`, hasta ~30 s). Activar filtro **"Solo con pendientes"**.
3. Identificar items con **"X espera" > 0**.
4. **IGNORAR todo lo que sea parcial**: nombre contiene "Parcial", o tipo `PARCIAL_x` / `RECUPERATORIO_x`. No se corrige por IA.

#### Paso 1 — Importar las entregas de Moodle

Las entregas "en espera" **no están en Active-IA todavía** (no salen en la tabla de `/entregas`). Para traerlas:

- En `/pendientes`, click **"Importar"** en la fila TP × Comisión correspondiente (o **"Importar materia"** / **"Importar todo"** para lotes grandes — más eficiente: un click trae todo lo realmente nuevo de ambas comisiones).
- Abre un modal **"Importar — <COMI/Materia>"** que descarga de Moodle en vivo ("Cargando N de M"). Al terminar muestra un resumen con categorías: **Cargadas** (nuevas) / **Re-entregas** / **Ya corregidas** / **Duplicadas** / **Sin archivos** / **Errores**. Las **Cargadas** quedan en estado **⚠️ Subida**.
- ⚠️ **"X espera" en `/pendientes` NO es lo mismo que "X para corregir".** Una espera puede ya estar corregida-sin-subir, ser duplicada, o ser una entrega **sin archivos** (entrega vacía → cuenta como error y no se carga). Es normal que importar 2 esperas dé "0 cargadas". Lo que de verdad baja la "espera" es **empujar la nota a Moodle** (Paso 3).
- *(Fallback manual: si alguna entrega no se importa — ej. formato raro — usar "Subir Entrega" individual, ver "Camino B (fallback)" abajo.)*

#### Paso 2 — Corregir con IA

1. Ir a `/entregas?comision_id=<X>&rubrica_id=<Y>` (el link sale de "Ver entregas" en `/pendientes`). Esperar 2-3 s el combo async.
2. Filtrar por estado **"Subidas"**.
3. Abrir el menú ⋮ de la fila. **El ⋮ no responde al click directo de Playwright** — abrirlo con `browser_evaluate` (reemplazar `N` por la fila 1-based):
   ```js
   () => { document.querySelectorAll('table tbody tr:nth-child(N) td:last-child button')[0].click(); }
   ```
4. `browser_snapshot` inmediato para los refs frescos del dropdown (el menú es un portal al final del DOM y se cierra con scroll/snapshot pesado).
5. Click **"Corregir"** (en una ya corregida el botón se llama **"Re-corregir"** — NO re-corregir sin pedido explícito).
6. Esperar a estado **✅ Corregida** + nota (puede tardar hasta ~30 s). Si tira error de API key → revisar `/perfil`.

#### Paso 3 — Devolver la nota a Moodle (híbrido)

**Camino A — lote automático (TPs aprobados):**
1. Ir a `/por-entregar`. Muestra las correcciones hechas sin subir, separadas en **"Automáticas (TP)"** vs **"Requieren comentario"**, con la nota ya como **Aprobado / Desaprobado**.
2. Revisar la lista de "Automáticas". Si son todos TPs Aprobados → click **"Entregar todo"** para subirlas a Moodle de una.
3. Las marcadas **"Requieren comentario"** o cualquier **Desaprobado** → NO van en el lote; pasan al Camino B.

**Camino B — individual con freno humano (casos borde):**
1. Se llega al mismo modal de dos formas: en `/por-entregar` con el botón **"Subir"** de la fila, o en `/entregas` sobre la fila **✅ Corregida** → ⋮ (evaluate) → **"Subir corrección a Moodle"**. (El "Subir" de `/por-entregar` **abre el modal**, no envía directo.)
2. Aparece el modal **"Subir corrección a Moodle — <ALUMNO>"** con:
   - **Nota que se enviará a Moodle**: `Aprobado` / `Desaprobado` (binaria; la decide Active-IA según la nota numérica).
   - **Comentario para el alumno**: textbox editable, autogenerado (ej. *"Bien [Nombre]! Revisá con detalle tu entrega PDF."*).
   - Aviso: *"Al final se agregará automáticamente: Te dejo tu devolución"* — la palabra "devolución" es el link al PDF (hosteado en el backend de Active-IA, abre en pestaña nueva). **No hace falta Drive.**
   - 🚩 **Posible advertencia de sobrescritura**: si la entrega **ya está calificada en Moodle por fuera de Active-IA**, el modal muestra *"Esta entrega ya está calificada en Moodle (por fuera de Active-IA). Si la enviás, vas a pisar la nota actual."* + un checkbox **"Sobrescribir la nota de todas formas"**. Sin tildarlo, "Enviar corrección" **no hace nada** (la cola no baja). → **CASO BORDE: NO sobrescribir sin OK explícito del tutor.** Cancelar y reportar.
   - Botones: **Cancelar** / **Enviar corrección**.
3. **Por defecto NO modificar el comentario autogenerado.** En un caso borde, mostrarle al tutor la nota (Aprobado/Desaprobado) + el comentario y **esperar su OK** antes de "Enviar corrección".
4. Click **"Enviar corrección"** → **verificar que la fila desaparezca de `/por-entregar` (el contador "Entregar todo (N)" baja en 1).** Si no baja, el envío NO se concretó (probable advertencia de sobrescritura sin tildar) — revisar el modal.

#### Camino B (fallback) — Subir una entrega a mano

Cuando "Importar" no agarra una entrega, o cuando hay que corregir una **reentrega** (ver abajo). Botón **"Subir Entrega"** (arriba a la derecha de `/entregas`):
1. Modo **"Entrega Individual"**. **Nombre del alumno EXACTO como en Moodle** (Active-IA usa el nombre como key).
2. ⭐ **(NUEVO 2026-06-30) Campo "URL de la entrega en Moodle (opcional)"**: pegá el grader URL del alumno (`mod/assign/view.php?id=<assign>&action=grader&userid=<X>`). **Habilita devolver la nota a Moodle desde Active-IA aun siendo carga manual** — resuelve el viejo TOPE del HTTP 400 (ver abajo).
3. Drop zone + `browser_file_upload` con el `.zip`. ⚠️ **El path debe estar bajo `/home/juanisarmiento`** (el MCP de Playwright rechaza `/tmp`: *"outside allowed roots"*).
4. ⚠️ **(NUEVO) Tras adjuntar aparece "Modo de Procesamiento" — paso OBLIGATORIO:** **Solo código** (.py, .java, .js, .ts, .c, .cpp, .go) / **Web completo** (+.html/.css/.json) / **Proyecto completo** (+.md/.txt/.yml/.xml) / **Personalizado**. Sin elegirlo, **"Subir Entrega" no envía** (parece colgado, no da error visible). Arrancar con **"Solo código"**; si es `.ipynb`, "Personalizado" + extensión `.ipynb`.
5. Subir → queda **⚠️ Subida** → seguir desde Paso 2 (Corregir).

> 🟢 **EL TOPE DEL HTTP 400 YA SE PUEDE RESOLVER (actualizado 2026-06-30).** Antes una carga manual NO se podía devolver a Moodle (quedaba "no vinculada", ⋮ → "Subir corrección a Moodle" tiraba **HTTP 400**). Ahora, **si llenás el campo "URL de la entrega en Moodle" al subir** (paso 2), la corrección queda vinculada y el ⋮ → "Subir corrección a Moodle" funciona. Si igual querés cargarla **a mano en Moodle** (más determinístico, o si el tutor lo pide): sacá la nota binaria + comentario + link `a[href*="devoluciones"]` del modal, **Cancelá**, y escribí en el grader del assign (`#id_grade` Aprobado/Desaprobado + comentario con el link en TinyMCE `#id_assignfeedbackcomments_editor` y su iframe → `savechanges`).

> 🛑 **GOTCHA HTTP 409 al subir manual (verificado 2026-06-30):** si el alumno **ya tiene una entrega para esa rúbrica** (p.ej. una corrección previa), "Subir Entrega" tira **HTTP 409 Conflict** (`POST /api/v1/entregas/`). El checkbox **"Sobrescribir si ya existe" NO lo resuelve**, y **Archivar tampoco libera el nombre/vínculo**. Hay que **ELIMINAR la entrega vieja primero**: filtrar por estado (la vieja está en "Corregidas", o en "Archivadas" si la archivaste) → ⋮ → **Eliminar** → confirmar el aviso *"se perderá permanentemente"*. Recién ahí subís la nueva sin conflicto.

> 🔁 **Reentregas (alumno volvió a entregar tras una corrección):** al **"Importar"**, Active-IA reporta *"N re-entregas — no se sobrescribieron, revisalas manualmente"* y **NO las auto-corrige** (la entrega sigue con la corrección vieja). Para calificar la versión nueva: **bajar el zip nuevo de Moodle** (la fila con la fecha de envío más reciente) → **eliminar la entrega vieja** (gotcha 409) → **Subir Entrega manual** con la URL de Moodle → Corregir.

**Bajar el zip de Moodle por fetch (con la sesión Playwright ya logueada en el campus):** `browser_evaluate` async con `fetch(href, {credentials:'include'})` → `arrayBuffer` → base64 (`btoa`); guardarlo con `filename` (⚠️ el valor sale **entre comillas JSON** — sacarlas con `tr -d '"'` antes de `base64 -d`). ⚠️ **El zip del alumno puede traer un `.mp4` grande** (video de microteaching, decenas de MB) junto al `.py` → **subir solo el código** (extraer el `.py` y rezipear, o confiar en "Solo código"). ⚠️ **Evitar `?forcedownload=1` en el fetch** — puede cerrar la página del MCP (*"Connection closed"*). Otros gotchas: archivo **sin extensión** dentro del zip (renombrar a `.py` y re-zipear), o **2 zips** (subir solo el del código).

#### Reporte final

Al terminar, tabla:

| Alumno | TP | Comisión | Nota (Aprob/Desaprob) | Estado |

para cada corrección devuelta a Moodle. Listar aparte los casos borde que quedaron esperando confirmación.

### Particularidades aprendidas de Active-IA (flujo nuevo)

- **El procesamiento NO se dispara solo** — tras "Importar"/"Subir Entrega" la entrega queda en **Subida**; hay que ir al ⋮ y clickear "Corregir".
- **El ⋮ se abre solo por `browser_evaluate`** (`tr:nth-child(N) td:last-child button`), no por click directo. Snapshot inmediato para refs.
- **El menú ⋮ de una Corregida** ofrece: Ver Entrega / **Ver / Editar Corrección** / **Re-corregir** / **Descargar PDF** / **Subir corrección a Moodle** / Archivar / Eliminar.
- **La nota a Moodle es binaria** (Aprobado/Desaprobado), aunque Active-IA guarde la numérica 0-100. El corte exacto lo decide Active-IA — en duda, revisar los que estén cerca del límite (caso borde → freno humano).
- **`/por-entregar` es el centro del flujo nuevo** y separa automáticas vs "requieren comentario". Hay correcciones **"no vinculadas a Moodle"** (cargadas a mano / sin config Moodle) que NO se pueden subir desde ahí — se manejan por el modal individual del Camino B si corresponde.
- **El conteo de alumnos no coincide con el campus** — Active-IA acumula entregas de cohortes previas (cientos de "corregidos"/"sin entrega"). Filtrar siempre por comisión + rúbrica + estado.
- **El combo de Rúbrica es async** — esperar 2-3 s al cargar `/entregas`.
- **Lock de Chrome (Playwright)**: si `browser_navigate` tira *"Browser is already in use … mcp-chrome-…"*, hay un Chrome huérfano. Matarlo (`pkill -9 -f <user-data-dir>`, **cuidado de no auto-matchear el propio comando**: partir el patrón en dos strings) y borrar `SingletonLock/Cookie/Socket` del user-data-dir antes de reintentar.
- **El browser se cierra solo a veces tras "Enviar corrección"** (*"No open pages available"* / *"Target page … has been closed"*). No asumir que falló: **re-navegar a `/por-entregar` y verificar por el contador "Entregar todo (N)"** si la entrega se fue de la cola. Enviar a Moodle es razonablemente idempotente (re-escribe la misma nota/comentario), pero verificá antes de reintentar para no dudar.
- **Caso real (corrida 2026-06-04, alumnos anonimizados)**: en una tanda de 6 corregidas de una comisión, 4 se subieron limpio. Frenados: **uno (TP6, Desaprobado)** por regla híbrida, y **otro (TP8, Aprobado)** porque ya tenía nota manual en Moodle (advertencia de sobrescritura). Es el patrón esperable: la mayoría va en lote, 1-2 quedan para freno humano.

### Cosas que NO hacer en el pipeline de Active-IA

- **NO corregir ni subir parciales/recuperatorios** (`PARCIAL_x`, `RECUPERATORIO_x`, nombre con "Parcial"). Solo TPs.
- **NO sobrescribir entregas ya corregidas** ("Re-corregir") sin pedido explícito.
- **NO enviar a Moodle un caso borde** (Desaprobado, nota al límite, "Requiere comentario") sin confirmación del tutor. El lote automático es solo para TPs Aprobados.
- **NO modificar el comentario autogenerado** del modal salvo que el tutor lo pida.
- **NO subir dos alumnos con el mismo nombre** sin desambiguar — Active-IA usa el nombre como key; agregar inicial del 2º apellido.
- **NO clickear "Todos los PDFs" / "Importar todo" / "Corregir todo" / "Entregar todo" sin pensar el alcance** — son operaciones masivas. Confirmá comisión/TP/estado antes.

### (Deprecado) Subida del PDF a Google Drive

El flujo viejo subía cada PDF de rúbrica a una carpeta de Drive por comisión. **Ya no es parte del flujo**: el PDF se hostea solo y se linkea en el comentario de Moodle. Mantener esto solo si el tutor pide explícitamente archivar copias en Drive (con sus propias carpetas/credenciales); en ese caso, `create_file` funciona aunque `canAddChildren` venga `false`, y verificar con `get_file_metadata`.
