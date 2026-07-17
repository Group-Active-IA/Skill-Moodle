---
name: tup-campus-navigator
description: Navegar y extraer datos del campus Moodle de la TUP (UTN) — Programación I (curso 38), II (curso 42) y III (curso 44). Activar cuando el usuario menciona "campus TUP", "moodle TUP", "tup.sied", "Programación 1/2/3", "comisión C1/C2/C3", "informe de seguimiento", entregas/parciales de alumnos, o necesita revisar entregas/calificaciones por grupo. También cubre **Active-IA** (`active-ia-correcion-automatica.vercel.app`) para corrección automática de TPs: importar entregas de Moodle, corregir con IA (Gemini) y devolver la nota a Moodle automáticamente vía el flujo "Subir corrección a Moodle" / "Por entregar". Usa Playwright MCP para login y operación con browser_evaluate. (El flujo de Drive quedó deprecado — el PDF de devolución ahora se hostea en el backend de Active-IA y se linkea solo en el comentario de Moodle.) También cubre **auditoría de corrección a nivel curso** (qué falta corregir / qué comisiones deben / quién tiene que corregir / cuántos pendientes, por TP/parcial/global y por tutor, sin importar comisión) usando el "Sumario de calificaciones" oficial de Moodle y reportes en PDF.
---

# TUP Campus Navigator — Programación I

Skill para que **cualquier tutor de Programación I** opere el campus Moodle de la Tecnicatura Universitaria en Programación (UTN) — `https://tup.sied.utn.edu.ar`. El tutor tiene permisos sobre una o más comisiones (grupos separados de Moodle) del curso de Programación I de la cohorte vigente.

> **Esta skill NO tiene datos personales hardcodeados.** La identidad del tutor, sus credenciales y sus comisiones se obtienen en el **Paso 0 — Bootstrap** (más abajo): se pregunta el nombre, se piden las credenciales y se **descubren los IDs en vivo** desde el propio Moodle. Las tablas de IDs que aparecen en este documento son **caché de referencia** de una cohorte concreta — el Paso 0 las verifica y, si cambiaron, usa los valores reales del campus.

## Cuándo activarla

- "Entrá al campus" / "abrí Moodle TUP" / "tup.sied"
- "Comisión 23" / "Comisión 25" / "C1-23" / "C1-25"
- "Informe de seguimiento", "deudas", "entregas pendientes", "quién no entregó"
- Revisar entregas, calificar, ver parciales, bajar `.zip`
- **"Active-IA" / "corregir TPs" / "evaluar entregas" / "rúbrica automática" / "importar entregas" / "subir corrección a Moodle" / "por entregar" / "devolución"** → arrancar el pipeline de corrección automática (ver sección "Active-IA — Pipeline de corrección automática")
- Mapear participantes, último acceso, alumnos en peligro
- **"Qué falta corregir" / "qué comisiones deben" / "quién tiene que corregir" / "cuántos pendientes" / "auditá todo el curso" / "sin importar comisión" / "por tutor"** → auditoría de corrección a nivel curso (ver sección "Auditoría de corrección a nivel CURSO")

## Paso 0 — Bootstrap (identidad + credenciales + verificación)

⚠️ **OBLIGATORIO antes de cualquier operación.** No confíes en las tablas de IDs cacheadas de este documento sin verificar — cambian por cohorte y por tutor. Este paso persona­liza la skill para quien la está usando.

### 0.1 — Identidad del tutor

Preguntar (si no están ya en memoria de la sesión):
- **Nombre completo del tutor** — tal como figura en Moodle. Sirve para excluirse del listado de estudiantes (aparece como "Profesor sin permiso de edición") y para firmar informes.
- **Credenciales Moodle**: usuario = **DNI sin puntos**, + contraseña. (Nunca escribir la contraseña a disco ni a memoria; usarla solo en el form de login.)
- **¿Usa Active-IA?** Si sí, pedir **usuario y contraseña de Active-IA** (cuenta propia del tutor) — ver sección Active-IA para el resto del setup (API key Gemini propia).

### 0.2 — Login

- Login URL: `https://tup.sied.utn.edu.ar/login/index.php` → completar usuario (DNI) + contraseña.
- Tras login redirige a `/my/` o al curso. **El campus mantiene sesión** toda la sesión Playwright — no reloguearse.

### 0.3 — Descubrir y verificar IDs (NO asumir los cacheados)

1. **Course ID**: navegar a `/my/` o a la lista de cursos, ubicar **"Programación I"** de la cohorte vigente y leer su `id` del link (`course/view.php?id=<COURSE_ID>`). NO asumir 38.
2. **Grupos del tutor (sus comisiones)**: ir a `user/index.php?id=<COURSE_ID>` y correr **Snippet 4** → lista los `group=<ID>` reales. Son las comisiones de ESTE tutor, no las de nadie más.
3. **assign / quiz / resource IDs**: desde cualquier página del curso, correr **Snippet 3** → mapea TPs, parciales y recursos del curso actual.
4. **Comparar contra la tabla cacheada** de abajo. Si coincide → la caché está vigente, usala. Si NO coincide → **cambió la cohorte o es otro curso**: avisar al tutor y usar SIEMPRE los IDs descubiertos en vivo (no los cacheados).

> Con esto la skill queda personalizada para el tutor que la corre, sin editar archivos.

Después del login, **el campus mantiene sesión** durante toda la sesión Playwright. No reloguearse.

## Identificadores clave — CACHÉ DE REFERENCIA (cohorte Marzo 2026)

⚠️ **Estos IDs son una caché de UNA cohorte/tutor concreto.** El **Paso 0** los verifica en vivo; si cambiaron, usar los descubiertos. Sirven como ejemplo de la estructura y como atajo cuando la verificación confirma que siguen vigentes.

### Curso

- Course ID: **38**
- URL base: `https://tup.sied.utn.edu.ar/course/view.php?id=38`
- Título: "Programación I-Marzo 2026" (breadcrumb: `P1_Marzo 2026`)

### Comisiones (grupos separados de Moodle)

| Comisión | Group ID | Notas |
|---|---:|---|
| **M26 C1-23** | **4292** | 24 estudiantes |
| **M26 C1-25** | **4299** | 24 estudiantes |

Filtro participantes por grupo: `/user/index.php?id=38&group=<GROUP_ID>&perpage=100`

### Secciones del curso

Cada TP ocupa 6 sub-secciones consecutivas: `Introducción` (la base) → `Actividades` → `Práctica` → `Microteaching` → `Autoevaluación` → `Encuesta de cierre`.

| Sección | section | Sub-secciones |
|---|---:|---|
| General | 0 | — |
| 1- Estructuras Secuenciales | 1 | 1→6 |
| 2- Estructuras Condicionales | 7 | 7→12 |
| 3- Estructuras Repetitivas | 13 | 13→18 |
| 4- Trabajo Colaborativo (Git) | 19 | 19→24 |
| 5- Listas | 25 | 25→31 |
| Repaso Parciales | 32 | — |
| 6- Funciones | 33 | 33→39 |
| 7- Datos complejos | 40 | 40→45 |
| 8- Manejo de errores | 46 | 46→51 |
| 9- Manejo de archivos | 52 | 52→57 |
| 10- Recursividad | 58 | 58→63 |
| Trabajo Integrador Obligatorio | 64 | — |
| Encuentros síncronos | 65 | — |
| Evaluaciones | 66 | — |
| Comisiones | 67 | equipo docente |
| Gestión Académica | 68 | — |
| ⚡ Active-IA | 69 | corrector externo |
| Cuadro de honor | 70 | — |

### Entregas — IDs de `mod/assign/view.php`

#### TPs (Actividad de cierre de cada unidad)

| TP | Unidad | assign id |
|---|---|---:|
| TP1 | Secuenciales | 11169 |
| TP2 | Condicionales | 11214 |
| TP3 | Repetitivas | 11237 |
| TP4 | Git / Colaborativo | 11191 |
| TP5 | Listas | 11261 |
| TP6 | Funciones | 11286 |
| TP7 | Datos complejos | 11312 |
| TP8 | Manejo de errores | 13761 |
| TP9 | Manejo de archivos | 11337 |
| TP10 | Recursividad | 11355 |
| TIO | Trabajo Integrador | 11393 |

#### Evaluaciones

| Instancia | Fecha | assign id |
|---|---|---:|
| 1er Parcial | 18/04/2026 | 11383 |
| 1er Parcial Extensión | 02/05/2026 | 16687 |
| Recuperatorio 1er Parcial | 10/05/2026 | 11381 |
| Inst. Extraordinaria 1er Parcial | 25/06/2026 | 11369 (copia: 16559) |
| 2do Parcial | 30/05–07/06/2026 | 11377 |
| Recuperatorio 2do Parcial | 16/06/2026 | 11375 |
| Inst. Extraordinaria 2do Parcial | 08/07/2026 | 11373 |
| Quiz Primer Parcial | — | quiz id 11385 |

#### Consignas / Rúbrica (`mod/resource/view.php`)

| Recurso | id |
|---|---:|
| Consigna 1er parcial 02/05 | 11368 |
| Consigna 2do parcial | 11372 |
| Consigna TIO | 11389 |
| Rúbrica TIO | 11390 |

## Patrones de URL

### Ver listado de entregas (vista de tutor)

```
https://tup.sied.utn.edu.ar/mod/assign/view.php?id=<ASSIGN_ID>&action=grading&group=<GROUP_ID>&status=&workflowfilter=&markingallocationfilter=&suspendedparticipantsfilter=0&tifirst=&tilast=&filter=
```

**Importante:** los parámetros vacíos al final son CRÍTICOS — sin ellos Moodle filtra por defecto a "Requiere calificación" y solo muestra 1-2 filas. Con todos los filtros vacíos se ven los 24 alumnos del grupo.

### Participantes por grupo

```
https://tup.sied.utn.edu.ar/user/index.php?id=38&group=<GROUP_ID>&perpage=100
```

### Curso → tema específico

```
https://tup.sied.utn.edu.ar/course/view.php?id=38&section=<SECTION_ID>
```

## Extracción de datos con `browser_evaluate`

### Snippet 1 — Listado de entregas (todos los alumnos del grupo)

Después de navegar a la URL de grading con filtros vacíos:

```js
() => {
  const tables = document.querySelectorAll('table');
  let gradingTable = null;
  tables.forEach(t => {
    const heads = Array.from(t.querySelectorAll('thead th')).map(h => h.textContent.toLowerCase());
    if (heads.some(h => h.includes('nombre')) && heads.some(h => h.includes('estado'))) gradingTable = t;
  });
  if (!gradingTable) return {rows: []};
  const rows = Array.from(gradingTable.querySelectorAll('tbody tr'))
    .filter(tr => tr.querySelectorAll('th,td').length >= 6)
    .map(tr => {
      const cells = Array.from(tr.querySelectorAll('th,td'));
      return {
        name: cells[1]?.textContent.trim().replace(/\s+/g,' ').slice(0,80) || '',
        status: cells[3]?.textContent.trim().replace(/\s+/g,' ').split('Acciones')[0].trim() || '',
        date: cells[5]?.textContent.trim().replace(/\s+/g,' ') || '',
      };
    });
  return {rowCount: rows.length, rows};
}
```

Estados típicos:
- `"Sin entrega"` — no entregó
- `"Sin entregaLa Tarea está retrasada por: ..."` — vencida sin entrega
- `"Enviado para calificar"` — entregó, pendiente de corrección
- `"Enviado para calificarCalificado"` — entregó y ya tiene nota

### Snippet 2 — Lista de participantes con último acceso

Navegar a `user/index.php?id=38&group=<X>&perpage=100`, luego:

```js
() => {
  const table = document.querySelector('table#participants, table.generaltable');
  const rows = Array.from(table.querySelectorAll('tbody tr'))
    .filter(tr => tr.querySelector('th,td')?.textContent.trim())
    .map(tr => Array.from(tr.querySelectorAll('th,td')).map(td => td.textContent.trim().replace(/\s+/g,' ')))
    .filter(r => r[1]);
  return {count: rows.length, rows};
}
```

Columnas: `[selector, Nombre, Email, Roles, Grupos, Último acceso]`. La columna "Último acceso" da el delta humano-legible (`"3 días 5 horas"`, `"Nunca"`, etc.).

### Snippet 3 — Extraer todos los IDs de assign/quiz/resource desde el sidebar

Útil cuando hace falta mapear IDs y no se conocen aún. Funciona desde cualquier página del curso porque el sidebar de Moodle lista todo:

```js
() => Array.from(document.querySelectorAll('a[href*="mod/assign/view.php"], a[href*="mod/quiz/view.php"], a[href*="mod/resource/view.php"]'))
  .map(a => ({text: a.textContent.trim().replace(/\s+/g,' ').slice(0,100), url: a.href}))
  .filter(x => x.text)
```

### Snippet 4 — Group IDs disponibles

Desde `user/index.php?id=38`:

```js
() => {
  const sels = Array.from(document.querySelectorAll('select')).map(s => ({
    id: s.id,
    opts: Array.from(s.options).map(o => ({v: o.value, t: o.textContent.trim()}))
  }));
  return sels.filter(s => s.id.includes('groups') || s.opts.some(o => o.t.includes('M26')));
}
```

## Convenciones para guardar datos

Cuando el dataset es grande (>50 filas), usar `filename` en `browser_evaluate` para escribir el JSON a disco:

```
filename: ~/.playwright-mcp/<descripcion>.json
```

Luego leerlo con `Read`. **No** intentar pasar el ratón sobre el DOM completo dentro del prompt — son MB de markup.

## Workflow — Informe de seguimiento académico

Cuando el usuario pide un informe de comisión, ejecutar este pipeline:

1. **Login** (si no está logueado).
2. **Participantes**: `user/index.php?id=38&group=<GID>&perpage=100` → snippet 2 → identificar `Estudiantes` (excluir al propio tutor, que aparece como "Profesor sin permiso de edición" — usar el nombre capturado en el Paso 0.1). Capturar último acceso.
3. **Por cada TP del rango pedido** (típicamente TP3→TP9 + parciales): navegar a la URL de grading con filtros limpios + snippet 1, guardar a `~/.playwright-mcp/<comision>_<tp>.json`.
4. **Cruce**: armar matriz alumno × TP. Calcular:
   - Deuda por alumno (cantidad de TPs `Sin entrega`)
   - Parcial a tiempo / atrasado / sin entregar
   - Último acceso → categorías `<30d` / `30–60d` / `>60d o Nunca`
5. **Categorías del informe**:
   - **Deuda 2 últimas**: alumnos cuyos TPs sin entregar son EXACTAMENTE los 2 últimos del rango.
   - **Deuda >2**: alumnos con ≥3 TPs sin entregar.
   - **Parcial a tiempo**: entrega calificada en el assign del parcial original (no extensión).
   - **Parcial atrasado**: entrega solo en el assign de la extensión.
   - **Sin parcial**: ninguna entrega en ninguno de los dos assigns.
   - **Recuperatorio atrasado**: sin entrega en el assign del recuperatorio + corresponde recuperar.
   - **En peligro**: 0 entregas Y último acceso ≥7 días (típicamente 7 deudas + 9–60 días sin entrar).
6. **Acciones realizadas**: este punto NO se extrae del campus — pedir al usuario que lo complete (foros donde publicó, mensajes enviados, calificaciones del período).

## Particularidades aprendidas

- **Recursividad (TP10) bloqueada parcialmente**: la sección 58 muestra intro y hoja de ruta libres. Lo que se bloquea es el bloque siguiente que requiere completar la encuesta de cierre de TP9 ("U9 — Tu opinión nos interesa").
- **Active-IA**: link externo a `https://active-ia-correcion-automatica.vercel.app/login` — corrector automático.
- **El header del Foro de Avisos de comisión** suele tener muchos mensajes no leídos (>20). Útil para verificar si el tutor publicó avisos recientemente.
- **Grupos auxiliares de entrega**: aparecen grupos tipo `Entrego_1er_examen_11_4`, `Grupo_2`, `Grupo_48`, `Grupo_79` que Moodle usa para condicionar la disponibilidad de certificados / TPs específicos. No confundir con las comisiones (M26 C1-XX).
- **"Nunca" en último acceso**: significa que el alumno se matriculó pero jamás entró al curso. Para la cohorte Marzo 2026 esto equivale a >60 días sin actividad.

## Cosas que NO hacer

- No clickear el combo de "Grupos separados" en la UI; pasar `group=<ID>` por URL es más rápido y reproducible.
- No usar el filtro "Estado" por defecto (filtra a "Requiere calificación" y oculta los `Sin entrega`).
- No bajar `.zip` masivamente sin confirmar con el usuario — son archivos de alumnos y puede consumir espacio.
- No publicar/enviar mensajes desde el tutor sin confirmación explícita — afecta a terceros.

---

## Auditoría de corrección a nivel CURSO (todas las comisiones)

> Cuándo: el usuario pide **"qué falta corregir", "qué comisiones deben", "quién tiene que corregir", "cuántos pendientes", "auditá todo el curso", "sin importar comisión", "por tutor"**. A diferencia del informe de seguimiento (que mira deudas de alumnos), esto mira **lo que los DOCENTES no calificaron todavía**, en todas las comisiones. Requiere una cuenta con visión global del curso (coordinación) — una cuenta de tutor común solo ve sus grupos.

### 🥇 Regla de oro: el conteo confiable es el "Sumario de calificaciones", NO contar filas

Contar filas de la tabla de grading (estado "Enviado para calificar") es **INESTABLE y da números equivocados** (verificado: para un mismo TP el row-scan daba 6, 5 y 4 en lecturas consecutivas — por filas duplicadas, render parcial y drift en vivo). **La verdad es el campo oficial de Moodle:**

- Abrir `mod/assign/view.php?id=<ASSIGN>` **SIN `action=grading`** → hay un "Sumario de calificaciones" (tabla de 2 columnas) con **Participantes / Enviados / Pendientes por calificar**.
- **`Pendientes por calificar`** = lo que falta corregir de ese assign. Ese número es el bueno.

### El sumario respeta el filtro por comisión (vía URL)

- `view.php?id=<ASSIGN>&group=0` → **todas** las comisiones (total del curso).
- `view.php?id=<ASSIGN>&group=<GROUP_ID>` → solo esa comisión (Participantes/Enviados/Pendientes de ESE grupo).
- ⚠️ **Moodle deja FIJA tu última comisión seleccionada entre sesiones.** Si abrís el assign sin `group`, puede mostrarte solo TU comisión (no el total). **Siempre forzar `group=0`** para el global o `group=<id>` para una puntual.

### El dato está VIVO (drift en tiempo real)

Mientras auditás, otros docentes pueden estar corrigiendo → "Pendientes" baja en vivo (Enviados queda igual, Pendientes baja = alguien calificando). Por eso:
- Leer **total (`group=0`) + desglose por comisión en UNA sola pasada** (un solo `browser_evaluate` con loop) para que sea internamente consistente (suma de comisiones = total).
- Si la suma por comisión < total `group=0`, la diferencia son **alumnos sin comisión M26 C1 asignada** o **drift entre lecturas** (re-leer en una pasada lo desambigua).
- **Ponerle fecha/hora al informe** y aclarar que es una foto que se mueve.

### Barrido eficiente: `fetch()` en loop dentro de un `browser_evaluate`

En vez de navegar página por página (decenas de `browser_navigate`), traé el HTML con `fetch(url, {credentials:'include'})` + `DOMParser` en un loop, en UNA llamada. Guardar resultados grandes con `filename`.

```js
// Pendientes por calificar OFICIAL, por instancia y por comisión, en una pasada
async () => {
  const G = {'C1-01':4149,'C1-02':4165,'C1-03':4206,'C1-04':4210,'C1-05':4214,'C1-06':4218,'C1-07':4222,'C1-08':4226,'C1-09':4230,'C1-10':4234,'C1-11':4238,'C1-12':4242,'C1-13':4246,'C1-14':4250,'C1-15':4261,'C1-16':4265,'C1-17':4269,'C1-18':4273,'C1-19':4277,'C1-20':4281,'C1-21':4285,'C1-22':4289,'C1-23':4292,'C1-24':4295,'C1-25':4299,'C1-26':4303,'C1-27':4307};
  const pend = async (id,gid) => {
    const r = await fetch(`https://tup.sied.utn.edu.ar/mod/assign/view.php?id=${id}&group=${gid}`,{credentials:'include'});
    const doc = new DOMParser().parseFromString(await r.text(),'text/html');
    let n=null;
    doc.querySelectorAll('tr').forEach(tr=>{const c=[...tr.querySelectorAll('th,td')]; if(c.length===2 && /Pendientes por calificar/i.test(c[0].textContent)) n=parseInt(c[1].textContent.replace(/\D/g,''))||0;});
    return n;
  };
  const id='17442'; // ejemplo: 2º Parcial (Entrega)
  const total = await pend(id,0); const bd={}; let sum=0;
  for (const [co,gid] of Object.entries(G)){ const p=await pend(id,gid); if(p>0){bd[co]=p; sum+=p;} }
  return { total, sumaComisiones:sum, sinComision: total-sum, breakdown: bd };
}
```

### Sacar los NOMBRES de los alumnos pendientes (y que cuadre)

El sumario da conteos, no nombres. Para nombres: tabla de grading (`action=grading&group=<id>&perpage=100`), fila pendiente = **estado contiene "Enviado para calificar" Y la columna Calificación (c[4]) está en "-"** (sin nota). Validar que la cantidad coincida con el sumario oficial de ese grupo.

```js
// dentro del DOMParser de cada (assign, group): columnas → c[1]=Nombre, c[3]=Estado, c[4]=Calificación
const grade = (c[4]?.textContent.replace(/\s+/g,' ').split('Acciones')[0]||'').trim();
const pendiente = /Enviado para calificar/i.test(c[3].textContent) && (grade==='-' || grade==='');
```

- **Corregir solo REDUCE pendientes** (no entran entregas nuevas a un parcial cerrado) → alcanza con barrer las comisiones que ya marcaron pendiente en el sumario (superset seguro), no las 27.
- ⚠️ **Gotcha de iniciales del avatar:** los alumnos **sin foto de perfil** muestran sus iniciales pegadas al nombre (`BMBAUTISTA MATTEI`, `NCNICOLAS RICARDO COMPAGNUCCI`) **tanto en grading como en el padrón** (`user/index.php`), así que cruzar contra el padrón NO lo arregla. Hay que **stripear el prefijo a mano** (son pocos; revisar uno por uno antes de mostrarlos en un informe).

### Mapa alumno → comisión → tutor

- **alumno → comisión:** `user/index.php?id=38&perpage=2000` trae TODO el padrón con la columna **"Grupos"** (incluye `M26 C1-XX`). Parsear el token `M26 C1-\d+`. (Cruzar nombres por `endsWith` por el tema del prefijo de iniciales.)
- **comisión → tutor (docente):** `user/index.php?id=38&group=<GID>&perpage=200` y filtrar la fila con rol que contiene **"Profesor"** (el tutor figura como "Profesor sin permiso de edición"). Así sabés "quién tiene que corregir".

### Caché de Group IDs — Programación I (curso 38, cohorte Marzo 2026)

C1-01 `4149` · C1-02 `4165` · C1-03 `4206` · C1-04 `4210` · C1-05 `4214` · C1-06 `4218` · C1-07 `4222` · C1-08 `4226` · C1-09 `4230` · C1-10 `4234` · C1-11 `4238` · C1-12 `4242` · C1-13 `4246` · C1-14 `4250` · C1-15 `4261` · C1-16 `4265` · C1-17 `4269` · C1-18 `4273` · C1-19 `4277` · C1-20 `4281` · C1-21 `4285` · C1-22 `4289` · C1-23 `4292` · C1-24 `4295` · C1-25 `4299` · C1-26 `4303` · C1-27 `4307`

(Verificar con Snippet 4; el combo de `user/index.php?id=38` lista TODAS las comisiones del curso, no solo las del tutor logueado.)

### Inventario de assign IDs de parciales/globales (descubiertos con Snippet 3)

| Instancia | assign id |
|---|---:|
| 1er Parcial | 11383 · Extensión 16687 · Recup 11381 · Extraord 11369/16559/17256 |
| 2do Parcial | **Entrega 17442** · 8-23hs 11377 · Recup 11375/**17509** · Extraord 11373/17650 |
| Global / Integrador (TIO) | anterior 11393 · **entrega 17595** · recup 17641 |

> Ojo: hay **varias instancias por parcial** (entrega real, copia, instancia sin uso). Barré TODAS y quedate con las que tengan `Pendientes por calificar > 0`. En la corrida del 22/06/2026 las que tenían pendientes eran 17442, 17509 y 17650.

### 🔁 Multi-curso: la MISMA auditoría aplica a Programación I, II y III

El método (sumario oficial + `group=<id>` + fetch-loop + mapa tutor) es **idéntico en los 3 cursos**; solo cambian `course_id`, el prefijo de comisión y los IDs. **Cada curso usa un prefijo distinto** (no asumir): Prog I = `M26 C1-XX`, Prog II = `A25 C2-XX`, Prog III = `M25 C3-XX`. Verificar siempre con Snippet 4 / `mod/assign/index.php?id=<curso>` por si cambió la cohorte.

| Curso | course_id | Comisiones | URL listado de assigns |
|---|---|---|---|
| Programación I | 38 | 27 · `M26 C1-XX` | `mod/assign/index.php?id=38` |
| Programación II | 42 | 10 · `A25 C2-XX` | `mod/assign/index.php?id=42` |
| Programación III | 44 | 15 · `M25 C3-XX` | `mod/assign/index.php?id=44` |

> 💡 Truco: `mod/assign/index.php?id=<curso>` lista TODOS los assigns del curso de una (más confiable que el sidebar de `course/view.php`, que carga lazy y devuelve 0).

**Programación II — Group IDs (curso 42):**
C2-01 `4311` · C2-02 `4315` · C2-03 `4319` · C2-04 `4323` · C2-05 `4327` · C2-06 `4331` · C2-07 `4335` · C2-08 `4339` · C2-09 `4343` · C2-10 `4347`

**Programación II — assigns (curso 42):** TPs Unidad 1-10 (Java): U1 `12138` · U2 `12162` · U3 `12184` · U4 `12207` · U5 `12230` · U6 `12252` · U7 `12272` · U8 `12291` · U9 `12306` · U10 `12321`. Integrador `12364`. Parciales: 1er `16578` · Recup 1er `16846` · Extraord 1er `17237` · 2do `17367` · Recup 2do `17579`.

**Programación III — Group IDs (curso 44, no contiguos):**
C3-01 `4137` · C3-02 `4138` · C3-03 `4145` · C3-04 `4153` · C3-05 `4157` · C3-06 `4251` · C3-07 `4169` · C3-08 `4255` · C3-09 `4178` · C3-10 `4182` · C3-11 `4186` · C3-12 `4190` · C3-13 `4194` · C3-14 `4195` · C3-15 `4199`

**Programación III — assigns (curso 44):** TPs (web/Spring): U2 CSS `13179` · U3 JS `13599` · TP Auth TypeScript `13740` · U5 `13245` · U6 Lombok/DTO `13286` · U7 Func `13311` · U9 Spring Boot `13348` · U10 APIs REST `13363`. Integradores/globales: Integrador HTML `13164` · Integrador JPA `13807` · Trabajo Integrador `15424` · JPA Hospital `13367` · **Global Mutantes** `13368` · Proyecto Final `13369`. Parciales: 1er `16609` · Recup 1er `17128` · Extraord 1er `17648` · 2do `17459` · Recup 2do `17581` · Extraord 2do `17647`.

> Caché tomada el 22/06/2026. La cuenta de coordinación ve TODAS las comisiones de los 3 cursos (acceso global). **Active-IA hoy solo cubre Prog I** (su combo de comisiones no tiene Prog II/III) → en Prog II/III la corrección es manual en Moodle; la auditoría funciona igual.

### Mapa comisión → docente (tutores), Prog II y III (22/06/2026)

Para informes "por docente" (quién debe corregir). Sacados del rol **Profesor** por comisión. ⚠️ **Gotcha:** algunos docentes NO figuran en el padrón global (`user/index.php?id=<curso>&perpage=2000`) porque no están como miembros del grupo en la columna "Grupos"; en esos casos hay que pedir el grupo puntual (`user/index.php?id=<curso>&group=<GID>&perpage=200`) y leer la fila Profesor. **Algunos tutores enseñan en los 2 cursos** (Sofia Raia, Juan Cruz Robledo, Renzo Sosa, Flor Gubiotti). **C3-07 tiene DOS docentes** (Matías Torres / Tomás Ferro).

**Prog II (C2):** C2-01 Jerónimo Felipe Cortez · C2-02 Marcos Vega · C2-03 Emmanuel Avellaneda · C2-04 Sofia Raia · C2-05 Renzo Sosa · C2-06 Flor Camila Gubiotti · C2-07 Flor Camila Gubiotti · C2-08 Juan Cruz Robledo · C2-10 Ramiro Hualpa (C2-09 s/d)

**Prog III (C3):** C3-01 Luciano Chiroli · C3-02 Luciano Chiroli · C3-03 Sofia Raia · C3-04 Sofia Raia · C3-05 Giuliano Espejo Mezzabotta · C3-06 Giuliano Espejo Mezzabotta · C3-07 Matías Torres / Tomás Ferro · C3-08 David Lopez · C3-09 Federico Frankenberger · C3-10 Federico Frankenberger · C3-11 Juan Cruz Robledo · C3-12 Juan Cruz Robledo · C3-13 Renzo Sosa · C3-14 Santiago Nicolás Videla · C3-15 Gianfranco Canciani

### Método rápido para "nombres por docente" (multi-comisión)

En vez de barrer comisión por comisión, es más eficiente:
1. **1 fetch group=0 por assign** a la vista de grading (`action=grading&group=0&perpage=2000`) → sacar los alumnos pendientes (estado "Enviado para calificar" + Calificación en "—").
2. Mapear cada alumno → comisión con el **padrón** del curso (`user/index.php?id=<curso>&perpage=2000`, columna "Grupos"; match exacto del nombre, ambos vienen de Moodle igual; fallback `endsWith` por el prefijo de iniciales).
3. comisión → docente con el mapa de arriba. Agrupar por docente.

### Gotchas específicos de Prog II / III (verificado 22/06/2026)

- **"Entregó y sin nota" ≠ "Pendientes por calificar" oficial.** El filtro Calificación-en-"—" puede dar un toque MENOS que el sumario oficial, porque Moodle cuenta como pendiente las **re-entregas que ya tenían nota previa** (status "Enviado para calificar" pero con nota vieja en la columna). Para "alumno que entregó y le falta nota" el filtro "—" es lo correcto; aclararlo en el informe.
- **Ítems sin campo "Pendientes por calificar":** en Prog III, `JPA del Hospital` (13367), `Global Mutantes` (13368) y `Proyecto Final` (13369) NO exponen ese campo en el sumario (otro tipo de calificación / no-entrega) → no entran al barrido automático; revisarlos a mano si interesan.
- **El Integrador de Prog II** (12364) tampoco devolvió "Pendientes por calificar" — mismo caso.
- **La sesión de Moodle se cae seguido** (de un día para otro y a veces dentro del día): si un barrido masivo devuelve TODO `None`/0, casi seguro la sesión expiró y los `fetch` cayeron al login → reloguear (Snippet de login: setear `#username`/`#password` y click `#loginbtn`) y reintentar.

### Informe en PDF (skill `pdf-official` → reportlab)

- `pip install reportlab --break-system-packages` si falta.
- Usar **Platypus** (SimpleDocTemplate + Table/TableStyle/Paragraph) para tablas prolijas.
- ⚠️ **Los emojis NO renderizan** con las fuentes default (salen como ☐). Usar caracteres simples coloreados (ej. `●` verde con `<font color="#1c7a4d">`), no `✅`.
- Verificar el PDF leyéndolo con la tool Read (renderiza la página) antes de darlo por bueno.
- Guardar en `~/Descargas/` si el usuario lo pide ("ponelo en descargas").

### Gotchas operativos de la auditoría

- **`perpage=2000`** en la URL de grading carga el padrón completo sin paginar (~663 alumnos; el resto son filas vacías de relleno).
- La **sesión de Moodle expira de un día para el otro** → si `fetch`/navegación redirige a `login/index.php`, reloguear (DNI + pass) y reintentar.
- El "Sumario de calificaciones" **solo aparece en la vista por defecto** del assign (sin `action=grading`); en `action=grading` no está.

---

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
