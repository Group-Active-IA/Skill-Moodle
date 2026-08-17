# Product

Panel local de `tup-campus-navigator`. Contexto de diseño de **esta superficie**.
El `PRODUCT.md` de la raíz del repo describe el **manual impreso**, que es otro
producto y otro register: no aplica acá.

## Register

product

## Users

**Tutores de la Tecnicatura Universitaria en Programación (UTN, modalidad a distancia).**
Docentes, no ingenieros de software. Muchos tutorean como segundo trabajo, en horarios
partidos, y corrigen de noche después de su otro laburo.

Cada tutor corre **su propio panel en su propia máquina**, con sus credenciales. No hay
servidor central ni estado compartido: es la misma topología que la skill.

El trabajo concreto que hacen todos los días: ver qué falta corregir, revisar la entrega
de un alumno, cargar nota con devolución, y no perder a los que dejan de entregar sin
avisar. Hoy todo eso pasa por la terminal, que funciona pero no se muestra ni se mira
de reojo.

## Product Purpose

Darle **superficie** a la skill sin cambiarle el criterio.

La primera pantalla es una conversación con el agente: el mismo que corre en la terminal,
con las mismas 44 tools y las mismas reglas. La segunda es Informes, donde vive la serie
de datos de las comisiones propias.

Éxito es que el tutor abra el panel a la mañana en vez de la terminal, y que en Informes
pueda contestar "¿cómo vengo?" sin pedirle nada a nadie.

**Lo que este producto NO es**: un lugar donde la IA fabrica números. El número lo produce
código determinístico (las tools); el agente lo lee, lo prioriza y explica qué mirar. Un
dato que no se puede reproducir dos veces no se puede validar, y esta herramienta se
construyó entera sobre poder validar.

## Brand Personality

**Calmo, preciso, sin ceremonia.** La voz de un colega que ya lo usó, no la de un
departamento de comunicación. Tres palabras: honesto, concreto, respetuoso del tiempo
ajeno.

Honesto porque la herramienta entera se construyó sobre "si no lo sé, lo digo": la
interfaz no puede mostrar un blanco tranquilizador donde hubo un error de relevamiento.
Emocionalmente: **competencia tranquila**. Esto lo hizo alguien que sabe, funciona, y no
te va a hacer perder la tarde.

## Anti-references

- **El chat de IA genérico.** Sidebar gris, acento violeta, burbujas, "✨ Ask me anything",
  sugerencias en chips de colores. Es el primer reflejo de la categoría y hay que salir de
  ahí sin romper la topología, que el usuario sí conoce y quiere.
- **El SaaS de plantilla.** Grillas de cards iguales, gradientes decorativos, el bloque
  hero-metric (número gigante + label chico + stats de apoyo), contenedores envolviendo
  todo.
- **El dashboard de analytics.** Semáforos, medidores, "Estado general: sano". Este
  producto tiene documentado por qué un resumen adjetivado es peligroso: un número raro
  alguien lo revisa, un "sano" no lo revisa nadie.
- **La doc de software 2026.** Clonar Linear o Stripe es la plantilla genérica de este año,
  no una identidad.

## Design Principles

1. **El humano decide, la IA ejecuta.** Toda escritura al campus se previsualiza y espera
   un OK explícito, dentro del hilo. Nunca un modal: un modal se cierra con Escape sin
   leer.
2. **Todo número muestra de dónde salió.** Qué tool lo produjo, de cuándo es la foto, y
   contra qué fuente independiente cierra. La procedencia no es un tooltip decorativo: es
   la única forma barata que este proyecto encontró de cazar un dato mal medido.
3. **Un blanco nunca tranquiliza.** `0`, lista vacía y "no se pudo relevar" son estados
   distintos y se ven distinto. Si una tool devuelve `degradado`, `omitido` o un aviso, la
   pantalla lo dice antes que el número.
4. **No resumir lo que no tiene denominador honesto.** Se muestra la forma del dato
   (distribución, matriz, espera en días), no un porcentaje de avance inventado.
5. **Calma sobre densidad.** El chat manda; el resto de la UI se corre del medio y aparece
   cuando suma.

## Accessibility & Inclusion

- Contraste WCAG AA como piso (4.5:1 cuerpo, 3:1 texto grande), verificado con cálculo.
- **El color nunca es el único portador de significado**: los estados llevan etiqueta
  textual además del color. La daltonía roja-verde es la más común, y el acento de este
  sistema es rojo.
- Foco visible en todo control. Navegable por teclado: el chat es lo primero que recibe
  foco al cargar.
- `prefers-reduced-motion` respetado.
