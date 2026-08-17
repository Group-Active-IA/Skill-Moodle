# Active-IA — corrección automática por rúbrica

Guía para un agente que tiene que operar esta integración. Todo lo de acá está
verificado contra la API en vivo (2026-08-17); donde algo no se pudo confirmar,
se dice.

## Qué es

Active-IA es un servicio **externo y aparte de Moodle** que corrige el código de
un TP contra una rúbrica cargada por la cátedra, usando Gemini del lado del
servidor. Devuelve una nota numérica sobre 100 y un PDF con el desglose criterio
por criterio.

No es parte del campus. Es una segunda opinión automatizada que el tutor cruza
con su propia lectura.

## Lo primero que hay que entender: NO escribe en Moodle

**`corregir_con_active_ia` deja la corrección en Active-IA y nada más.** No toca
la calificación del campus. Para que la nota llegue al legajo del alumno hay que
llamar `cargar_nota`, que es otra tool y pide su propia confirmación.

Consecuencias prácticas, y las dos ya causaron confusión real:

- **Los contadores de `activeia_pendientes` son de MOODLE, no de Active-IA.**
  `corregidos: 0` significa "ninguna tiene nota cargada en el campus", NO
  "Active-IA no corrigió nada". El 2026-08-04 eso hizo concluir que no se había
  corregido nada cuando ya había dos correcciones hechas. Para ver lo que
  Active-IA realmente corrigió está `activeia_correcciones`.
- **Un alumno puede figurar "pendiente de corrección" en el panel y estar
  corregido en Active-IA.** No es un bug: en Moodle sigue sin nota, y Moodle es
  lo que ve el alumno.

## Cómo se conecta

| | |
|---|---|
| Base URL | `https://api.active-ia.com/api/v1` (override: `ACTIVEIA_URL`) |
| Auth | JWT Bearer. `POST /auth/login` con `{username, password}` → `access_token` |
| Credenciales | `ACTIVEIA_USER` / `ACTIVEIA_PASS`, en el `.env` del tutor. Las carga la tool `configurar` |
| Gemini | Corre **del lado de Active-IA**. El tutor NO pasa ninguna API key de Gemini |

El token se cachea en memoria y se re-loguea solo ante un 401. Cada request usa
un cliente HTTP efímero, igual que el `MobileWSClient` de Moodle.

**Timeout de 90 s y no 30**: `GET /pendientes/moodle` medido tres veces tardó
25, 40 y 24 segundos. Con 30 fallaba una de cada tres llamadas.

Endpoints que la skill usa:

```
POST /auth/login
GET  /pendientes/moodle                        el mapa Moodle ↔ Active-IA
GET  /comisiones/
GET  /rubricas/?materia_id=N                   listado (funciona con rol tutor)
GET  /rubricas/{id}                            detalle — 403 con rol tutor
POST /entregas/                                multipart: archivo + metadatos
POST /correcciones/entregas/{id}/corregir      dispara Gemini (async)
GET  /correcciones/entregas/{id}               poll del resultado
```

## El modelo de datos, y dónde se rompe

```
materia (19 = Prog 1, 22 = Prog 4)
  └── unidad  ← se cruza con Moodle por `cmid` (el assign_id)
        ├── rubrica_id
        └── comisiones  ← se cruzan con Moodle por `groupId`
```

**El cruce `cmid` → unidad lo mantiene Active-IA, y está incompleto.** Ése es el
problema central de esta integración y el que más tiempo hace perder.

Medido el 2026-08-17:

| Materia | Rúbricas cargadas | Cruzadas con Moodle | Huérfanas |
|---|---|---|---|
| Prog 1 (19) | 9 | 3 | **6** |
| Prog 4 (22) | 9 | 9 | 0 |

Seis rúbricas de Prog 1 existían y ninguna tool las encontraba, porque
`activeia_resolver` busca por `cmid` y esas unidades no lo tienen asociado.

### La regla que sale de ahí

> **Un error del resolver NO es prueba de que no haya rúbrica.**

Cuando `activeia_resolver` devuelve *"No encontré la tarea con cmid=X"*, lo único
que probó es que Active-IA no mapeó ese cmid. La rúbrica puede existir. Antes de
decirle al tutor que hay que corregir a mano, mirá el listado
(`GET /rubricas/?materia_id=N`, que sí funciona con rol tutor) o pedile que abra
el panel de Active-IA.

Ese error ya llevó dos veces a corregir a mano una unidad que tenía rúbrica.

## El mapa local de respaldo

Para las huérfanas hay `~/.moodle-skill/activeia_rubricas.json`, que el tutor
completa a mano. Vive en su carpeta personal y **no en el repo**: son ids que
vencen sin avisar y no son iguales para todos.

```json
{
  "17792": {
    "rubrica_id": 149,
    "materia_id": 19,
    "titulo": "Práctico 5: Listas",
    "verificado": true,
    "como": "Se corrigió con esta rúbrica y el desglose habla de listas."
  }
}
```

`activeia_resolver` lo consulta **sólo** cuando `/pendientes/moodle` no encontró
el cmid, y la respuesta declara que el dato salió de ahí. El `comision_id` NO se
guarda en el archivo: se resuelve en vivo de cualquier otra unidad de la misma
materia, porque un id copiado a mano es un id que vence.

### `verificado` no es decorativo

Un par confirmado y uno deducido por número de unidad **no valen lo mismo**, y la
respuesta los distingue: el deducido devuelve `_meta.degradado: true` y un aviso
que empieza con ⚠️.

**Una rúbrica equivocada no da una nota floja: corrige otra cosa.** Corregir un
TP de listas con la rúbrica de condicionales produce un número plausible y sin
sentido, y ese número termina en el legajo de una persona. Si el par no está
verificado, decíselo al tutor antes de correr la corrección, no después.

## Las cuatro tools

| Tool | Qué hace | Escribe |
|---|---|---|
| `activeia_pendientes()` | El mapa del tutor: materias → unidades → comisiones, con `rubrica_id` cuando se puede inferir | no |
| `activeia_resolver(assign_id, group_id)` | Resuelve `comision_id` + `rubrica_id` para una tarea concreta | no |
| `activeia_correcciones(comision_id)` | Lo que Active-IA corrigió de verdad, con su nota | no |
| `corregir_con_active_ia(...)` | El flujo completo | en Active-IA, **no en Moodle** |

Cada tutor ve **sólo sus comisiones**. Los `comision_id` y `rubrica_id` no son
globales: salen de la cuenta logueada. Nunca asumas un id de otra cuenta.

## El flujo de corrección, paso a paso

1. Baja el archivo del alumno de Moodle por API REST (sin navegador).
2. `POST /entregas/` con el archivo y los metadatos. Un **409** significa que ya
   existía: devuelve `conflicto: true` y **no reintenta solo**.
3. Dispara la corrección (Gemini, asíncrono).
4. Hace poll cada 5 s hasta `CORREGIDA`, `ERROR` o timeout (180 s por defecto).
5. Descarga el PDF de devolución a `~/.moodle-skill/salidas/`.

### `GEMINI_OVERLOADED`: no vuelvas a disparar

Es el error más frecuente y el que más fácil se maneja mal. **No significa que la
corrección se perdió**: significa que la respuesta no llegó a tiempo. La entrega
quedó subida y muchas veces termina bien minutos después.

Si reintentás a ciegas, la subís de nuevo. Lo correcto es mirar primero
`activeia_correcciones(comision_id)`, que dice lo que Active-IA corrigió **de
verdad**. Y si igual reintentás, la skill detecta la entrega ya subida y retoma
ese trabajo en vez de duplicarlo — pero eso es una red, no un plan.

El timeout del servicio de IA se reporta como error para reintentar, **nunca como
una nota**.

Es una operación que cuesta plata y tiempo de cómputo: `confirmado=false`
devuelve un preview sin ejecutar nada.

## Permisos: qué puede un tutor y qué no

Con rol de tutor:

- **Sí**: listar rúbricas de su materia, ver sus comisiones, subir entregas,
  disparar correcciones, bajar los PDF.
- **No**: `GET /rubricas/{id}` devuelve **403 "Se requiere rol de coordinador o
  administrador"**. O sea, un tutor puede saber que la rúbrica 152 se llama
  "Práctico 8: Manejo de errores" pero no puede leer sus criterios por API.

Eso importa para verificar un par dudoso: por API no se puede. Hay que cruzarlo
contra otra fuente (la consigna oficial de la cátedra, por ejemplo) o abrir el
panel web.

## Gotchas

**El listado de rúbricas se traga los errores.** `_rubricas_de_materia` devuelve
`[]` ante cualquier problema — incluido un fallo de autenticación. "Esta materia
no tiene rúbricas" y "no me pude loguear" se ven exactamente iguales. Si te
devuelve vacío, no concluyas que no hay rúbricas.

**Active-IA acumula entregas de cohortes previas.** El conteo de alumnos puede no
coincidir con el campus. Filtrá siempre por comisión + rúbrica + estado.

**La rúbrica puede declarar una penalización que el motor no aplica.** Caso real
del 2026-08-17: el criterio C5 decía *"se aplica una reducción del 30% del total
de la nota final"* y la nota final era la suma limpia de los criterios
(48+14+15+10+0 = 87). Con el descuento habría dado ~61. Además el desglose
mostraba C5 en 0/10 cuando sus subcriterios sumaban 5. **Sumá los criterios y
comparalos con el total antes de dar la nota por buena.**

**Active-IA distingue presencia, no vínculo.** Verifica que algo se haya
instanciado, no que las instancias queden conectadas entre sí. Caso control: le
dio 100/100 a una entrega con "3 categorías OK" y "10 productos OK" donde ningún
producto quedaba vinculado a ninguna categoría; otro alumno que sí vinculaba
sacó lo mismo.

**Elogia como correcto lo que está hardcodeado.** Le puso puntaje completo a una
"búsqueda" que era `if puntajes[i] == 990`, con el valor escrito a mano.

**Puede recomendar cosas que la cátedra prohíbe.** Sugirió `try/except` en
Programación 1, donde la consigna lo veda y lo repite tres veces. El descuento de
fondo era correcto; la herramienta propuesta, no.

**No todo pasa por Active-IA.** Los TP de Git son URLs a un repo, no código
subido: se revisan a mano. No fuerces la corrección automática donde no aplica.

## El criterio con el que se usa

Active-IA gana donde tiene la rúbrica cargada y el humano no la leyó: hace
cumplir reglas explícitas de la consigna que a ojo se pasan por alto.

Pierde donde hay que entender si el programa **responde la pregunta**: cuenta
que existan las piezas, no que funcionen juntas.

Por eso el modo que funciona es el cruce: correr Active-IA, leer la entrega, y
comparar las dos lecturas. Cuando difieren, casi siempre las dos tienen razón en
algo distinto. Y la nota la decide el tutor, siempre.

Si vas a mandarle al alumno el PDF de Active-IA junto con una devolución propia,
**leelos juntos primero**: si se contradicen, el alumno no sabe a cuál hacerle
caso. O mandás uno solo, o decís explícitamente cuál manda.
