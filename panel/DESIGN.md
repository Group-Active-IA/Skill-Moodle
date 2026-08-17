# Design

Sistema visual del **panel** de Campus Navigator (pantalla).
El `DESIGN.md` de la raíz del repo describe el **material impreso** (manual y quick-start):
comparte identidad con este, pero no valores. Ahí `Motion: Ninguna`; acá no.

## Visual Theme

**Metáfora: cuaderno de corrección, llevado a pantalla.** La misma que el manual, y por la
misma razón: es la lógica visual del oficio del lector. Tinta sobre papel, marcas que
significan algo, lo importante señalado en rojo. No es decoración temática, define dónde va
cada cosa y de qué color.

Esto ata los dos productos: quien leyó el manual reconoce el panel, y el rojo significa lo
mismo en los dos.

**Claro por defecto, oscuro por toggle.** La escena que lo decide: *un tutor corrigiendo a
las 23hs después de su otro trabajo, con el panel en media pantalla y el campus Moodle en la
otra.* El campus es blanco y no se puede cambiar. Un panel oscuro obliga a la pupila a
saltar de brillo cada vez que se mira al otro lado, cincuenta veces por sesión. Gana el
claro. El oscuro queda para el que cerró el campus y sólo conversa.

**Lanes evitadas.** Primer reflejo de la categoría: *chat de IA → oscuro, sidebar gris,
acento violeta o azul*. Segundo reflejo: *chat que no es ChatGPT → editorial-typographic
(serif display + labels mono + filetes)*, que además ya venía saturada. Se evitan las dos:
la topología es familiar porque el usuario la conoce, la piel es del oficio del usuario.

## Color Palette

Estrategia: **Restrained**. Tinta oscura sobre papel apenas cálido, con un solo acento rojo
usado con disciplina. Ningún `#000` ni `#fff`: todo tintado.

Los valores del modo claro salen de `manual/estilo.css`, con el contraste ya verificado por
cálculo, no estimado.

### Claro

| Rol | OKLCH | Uso |
|---|---|---|
| `--papel` | `oklch(0.988 0.004 85)` | fondo del hilo |
| `--papel-nota` | `oklch(0.967 0.006 80)` | sidebar, input, burbuja del tutor |
| `--papel-code` | `oklch(0.955 0.008 255)` | bloques de código y salidas |
| `--tinta` | `oklch(0.24 0.016 255)` | cuerpo, títulos, botón primario |
| `--tinta-media` | `oklch(0.46 0.014 255)` | metadatos, procedencia del dato |
| `--tinta-tenue` | `oklch(0.63 0.011 255)` | numeración, marcas de tiempo |
| `--correccion` | `oklch(0.505 0.196 27)` | **único acento** |
| `--correccion-suave` | `oklch(0.955 0.022 27)` | fondo del bloque de confirmación |

Contraste verificado en el manual y reusado: cuerpo 15.89:1 · metadatos 6.88:1 · acento
6.28:1 · texto sobre fondo de advertencia 5.66:1.

### Oscuro

Invierte la metáfora sin invertir los roles: tinta clara sobre papel de noche. El rojo sube
en lightness para mantener contraste sobre fondo oscuro. **Los valores se verifican por
cálculo antes de commitear, nunca se estiman.**

| Rol | OKLCH |
|---|---|
| `--papel` | `oklch(0.185 0.008 255)` |
| `--papel-nota` | `oklch(0.225 0.009 255)` |
| `--papel-code` | `oklch(0.255 0.010 255)` |
| `--tinta` | `oklch(0.945 0.005 85)` |
| `--tinta-media` | `oklch(0.72 0.010 255)` |
| `--tinta-tenue` | `oklch(0.58 0.010 255)` |
| `--correccion` | `oklch(0.68 0.170 27)` |
| `--correccion-suave` | `oklch(0.30 0.060 27)` |

### Regla del acento

**El rojo aparece sólo donde el tutor puede hacer algo irreversible**: cargar una nota,
enviar un mensaje, publicar en un foro. Nunca decorativo, nunca en un botón que no escribe.

Consecuencia deliberada: **el botón de enviar del chat es tinta, no rojo.** Mandar un
mensaje al agente no escribe en el campus. Así, cuando aparece el bloque de confirmación, es
el único rojo de la pantalla y no compite con nada.

Estados, en texto e íconos, nunca como fondos grandes: verificado `oklch(0.55 0.12 150)` ·
atención `oklch(0.62 0.13 75)` · error, el mismo `--correccion`.

## Typography

Las tres familias del manual, por las mismas razones. **Rechazadas por reflejo de training
data**: Inter, IBM Plex, JetBrains Mono, y el stack `-apple-system` que en Linux no resuelve
a nada.

| Familia | Rol | Por qué |
|---|---|---|
| **Atkinson Hyperlegible** | cuerpo, mensajes | Del Braille Institute, diseñada para máxima legibilidad. Se lee cansado y de noche: la elección responde al uso. |
| **Public Sans** | títulos, labels, navegación | Del sistema de diseño del gobierno de EEUU, hecha para formularios oficiales. Institucional sin ser corporativa; 9 pesos dan jerarquía real. |
| **Fragment Mono** | código, salidas, números de tabla | Carácter propio, no la mono de reflejo. |

Se empaquetan locales (`woff2`) en el build. Nada de CDN: el panel tiene que arrancar sin
internet más allá del campus.

Escala, ratio 1.25, contraste por peso:

- display (bienvenida): `30px / 600`
- título de vista: `20px / 600`
- cuerpo del hilo: `15.5px / 400`, line-height `1.65`
- labels y metadatos: `13px / 500`, tracking `+0.01em`
- micro (procedencia, timestamps): `12px / 500`

Ancho de lectura del hilo topeado a **68ch**, igual que el manual.
`font-variant-numeric: tabular-nums` global: las tablas de datos se alinean solas.

## Spacing & Layout

Grilla base 4px. Ritmo variado, nunca padding uniforme.

- **Sidebar** 260px, silenciosa, sobre `--papel-nota`.
- **Hilo** centrado, ancho de lectura ~720px, con aire generoso arriba y abajo.
- **Input** fijo abajo, separado del hilo por aire, no por una línea.

Radios: control chico 10px, input y burbuja 14px, paneles 20px.

Cero card dentro de card. El contenido respira sobre la superficie sin contenedores de más:
un mensaje del agente no va en una tarjeta, va sobre el papel.

## Elevation & Depth

Profundidad sutil, una sola dirección: `0 1px 2px` y `0 8px 24px` muy tenues, con el croma
del neutro. Nada de glassmorphism decorativo. Translúcido con `backdrop-blur` leve **sólo en
dos lugares y con intención**: la barra superior y el contenedor del input al hacer scroll,
para que el texto pase por debajo sin chocar.

## Motion

Ease-out exponencial `cubic-bezier(0.16, 1, 0.3, 1)`, 160 a 240ms. Sin bounce, sin elastic.

- Entrada de mensaje: fade + `translateY(6px)`.
- El bloque de confirmación **no anima la entrada**: aparece firme. Lo irreversible no se
  desliza.
- Nunca animar propiedades de layout. Sólo `transform` y `opacity`.
- `prefers-reduced-motion: reduce` desactiva todo movimiento, deja los cambios de opacidad.

## Components

| Componente | Qué comunica |
|---|---|
| `.mensaje--tutor` | lo que escribe el tutor. Burbuja tenue sobre `--papel-nota`, alineada a la derecha. |
| `.mensaje--agente` | la respuesta. Ancho completo, sin burbuja, máxima legibilidad. |
| `.confirmar` | **lo irreversible.** Único lugar con superficie roja. Inline en el hilo, jamás modal. Dos acciones: Confirmar, Ajustar. |
| `.procedencia` | de qué tool salió el número, de cuándo es la foto, contra qué cierra. Micro, `--tinta-media`, siempre presente en un dato. |
| `.aviso` | `degradado`, `omitido`, recorte declarado. Va **antes** del número, no después. |
| `.salida` | lo que devolvió la herramienta, sin maquillar. Mono sobre `--papel-code`. |
| `.vacio` | una frase y qué hacer. Nunca "¡Todo al día!" sin haber relevado. |

## Estados que no se pueden saltear

Cada vista los implementa los cinco. Este producto tiene documentado que los bugs viven
justo acá, no en el flujo feliz.

1. **Default** con dato.
2. **Vacío verificado**: se relevó y no hay nada. Se puede decir "al día".
3. **Vacío no verificado**: no se pudo relevar. **Se ve distinto del anterior** y nunca
   tranquiliza.
4. **Degradado**: hay dato pero con huecos declarados. El aviso va arriba.
5. **Error**: una línea legible, sin alarmismo, con qué reintentar.

Los estados 2 y 3 son opuestos y son el bug más caro de la historia de este proyecto. Que se
vean iguales es un defecto de diseño, no un detalle.

## Accessibility

- AA verificado con cálculo, no estimado. Script de verificación junto al CSS.
- El color nunca solo: todo estado lleva etiqueta textual.
- Foco visible en todos los controles, con `:focus-visible`.
- El input del chat recibe foco al cargar.
- `prefers-reduced-motion` respetado.
