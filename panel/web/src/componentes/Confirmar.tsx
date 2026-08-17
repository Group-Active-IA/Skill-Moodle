/*
  El bloque de confirmación.

  Es lo único que separa este chat de un chat cualquiera: acá se escribe en el
  legajo de una persona, y una vez escrito no se deshace desde la API.

  Tres decisiones que no son de estilo:
  - Vive en el hilo, nunca en un modal. Un modal se cierra con Escape sin leer.
  - No anima la entrada. Lo irreversible no se desliza.
  - El foco arranca en Ajustar, no en Confirmar. Un Enter distraído no carga
    una nota.
*/

import { useEffect, useRef, useState } from 'react'

type Props = {
  tool: string
  irreversible: boolean
  entrada: Record<string, unknown>
  onDecidir: (ok: boolean, entrada?: Record<string, unknown>) => void
}

// Cómo se llaman los campos en castellano, para no mostrarle `assign_id` a un
// docente. Lo que no esté acá se muestra con su nombre crudo: inventar una
// traducción sería peor que mostrar el campo real.
const ETIQUETAS: Record<string, string> = {
  email: 'Alumno',
  alumno: 'Alumno',
  nota: 'Nota',
  mensaje: 'Devolución',
  texto: 'Texto',
  asunto: 'Asunto',
  assign_id: 'Actividad',
  group_id: 'Comisión',
  forum_id: 'Foro',
  post_id: 'Respuesta a',
}

const OCULTOS = new Set(['confirmado'])

export function Confirmar({ tool, irreversible, entrada, onDecidir }: Props) {
  const [editando, setEditando] = useState(false)
  const [valores, setValores] = useState(entrada)
  const ajustar = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    ajustar.current?.focus()
  }, [])

  const campos = Object.entries(valores).filter(([k]) => !OCULTOS.has(k))

  return (
    <div className="confirmar" role="group" aria-label="Confirmación de escritura">
      <div className="confirmar__titulo">
        {irreversible ? 'Va a escribir en el campus' : 'Necesita tu OK'}
      </div>

      <dl className="confirmar__campos">
        {campos.map(([clave, valor]) => (
          <div key={clave} style={{ display: 'contents' }}>
            <dt>{ETIQUETAS[clave] ?? clave}</dt>
            <dd>
              {editando ? (
                <textarea
                  className="confirmar__editable"
                  value={String(valor ?? '')}
                  rows={String(valor ?? '').length > 70 ? 4 : 1}
                  onChange={(e) => setValores({ ...valores, [clave]: e.target.value })}
                />
              ) : (
                String(valor ?? '')
              )}
            </dd>
          </div>
        ))}
      </dl>

      <div className="confirmar__acciones">
        <button
          className="boton boton--escribe"
          onClick={() => onDecidir(true, editando ? valores : undefined)}
        >
          {irreversible ? 'Cargar' : 'Confirmar'}
        </button>
        <button
          ref={ajustar}
          className="boton boton--secundario"
          onClick={() => (editando ? onDecidir(false, undefined) : setEditando(true))}
        >
          {editando ? 'Cancelar' : 'Ajustar'}
        </button>
        {irreversible && (
          <span className="confirmar__nota">Esto no se deshace desde el campus.</span>
        )}
      </div>

      <span className="confirmar__nota">
        Herramienta: <code>{tool}</code>
      </span>
    </div>
  )
}
