/*
  Un mensaje del agente, con markdown renderizado.

  Se sanitiza siempre. No es paranoia de manual: por este hilo pasa el código de
  las entregas de los alumnos, que es contenido de terceros. Un `<script>` o un
  `<img onerror>` dentro de un TP no debería poder ejecutar nada en el panel de
  un tutor que tiene la sesión del campus abierta.
*/

import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { useMemo } from 'react'

marked.setOptions({ breaks: true, gfm: true })

export function Mensaje({ texto }: { texto: string }) {
  const html = useMemo(() => {
    const crudo = marked.parse(texto, { async: false })
    return DOMPurify.sanitize(crudo, {
      // Nada de formularios, iframes ni handlers: acá sólo se lee.
      FORBID_TAGS: ['style', 'form', 'input', 'iframe', 'object', 'embed'],
      FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick'],
    })
  }, [texto])

  return (
    <div
      className="mensaje mensaje--agente"
      // Seguro: lo de arriba pasó por DOMPurify.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
