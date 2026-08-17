/*
  La procedencia de la foto, con su botón de actualizar.

  Vive en un componente propio porque las tres vistas comparten el mismo
  relevamiento y tienen que decir lo mismo: si una dice «hace 3 min» y otra
  «recién» sobre el mismo dato, el lector no sabe a cuál creerle.

  El botón existe porque sin él la única forma de refrescar era esperar a que se
  venciera el caché de diez minutos. Recargar la página no alcanzaba: la foto
  vive del lado del servidor, no del navegador.
*/

import { hace } from '../lib/unidades'

export type Procedencia = {
  tool?: string
  consultas: number
  fallaron: number
  relevado_at: number
  desde_cache?: boolean
  degradado?: boolean
}

type Props = {
  procedencia: Procedencia | null
  relevando: boolean
  onActualizar: () => void
}

export function Foto({ procedencia, relevando, onActualizar }: Props) {
  return (
    <span className="foto">
      <span className="dia__fuente">
        {relevando
          ? 'Relevando el campus…'
          : procedencia
            ? `${procedencia.consultas} consultas · sumario · ${hace(
                procedencia.relevado_at,
              )}`
            : 'sin datos'}
      </span>
      <button
        className="foto__actualizar"
        onClick={onActualizar}
        disabled={relevando}
        title="Volver a preguntarle al campus. Tarda unos 30 segundos."
      >
        {relevando ? '…' : 'Actualizar'}
      </button>
    </span>
  )
}
