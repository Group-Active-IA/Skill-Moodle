/*
  La foto del día, una sola para todo el panel.

  Antes cada vista hacía su propio pedido. Con el caché del servidor casi siempre
  coincidían, pero «casi siempre» no alcanza: bastaba que una vista pidiera justo
  antes de que venciera el caché y otra justo después para que dos pantallas
  mostraran números distintos del mismo campus, sin que ninguna avisara. Es el
  error de fondo de este proyecto en versión interfaz — dos cosas que se
  contradicen y ninguna se declara.

  Ahora hay un solo pedido y un solo estado. Cuando alguien actualiza, las tres
  pantallas cambian juntas.
*/

import { useEffect, useState } from 'react'

export type Celda = {
  unidad: string
  titulo: string
  assign_id: string
  enviados: number | null
  pendientes: number | null
  motivo?: string
}

export type Comision = {
  curso: string
  course_id: number
  comision: string
  group_id: number
  participantes: number | null
  pendientes: number
  donde: { unidad: string; titulo: string; pendientes: number }[]
  fallaron: { titulo: string; motivo: string }[]
  degradado: boolean
  detalle: Celda[]
}

export type Procedencia = {
  tool?: string
  consultas: number
  fallaron: number
  relevado_at: number
  catalogo_at?: string | null
  desde_cache?: boolean
  degradado?: boolean
}

export type Foto = { comisiones: Comision[]; procedencia: Procedencia }

type Estado = {
  foto: Foto | null
  relevando: boolean
  error: string | null
}

let estado: Estado = { foto: null, relevando: false, error: null }
const oyentes = new Set<(e: Estado) => void>()
let pedido: Promise<void> | null = null

function emitir(nuevo: Partial<Estado>) {
  estado = { ...estado, ...nuevo }
  for (const o of oyentes) o(estado)
}

export function pedirFoto(refrescar = false): Promise<void> {
  // Un solo pedido en vuelo. Sin esto, montar las tres vistas dispara tres
  // relevamientos de 44 consultas cada uno contra el servidor de la facultad.
  if (pedido && !refrescar) return pedido
  if (estado.foto && !refrescar) return Promise.resolve()

  emitir({ relevando: true, error: null })
  pedido = fetch(`/api/dia${refrescar ? '?refrescar=true' : ''}`)
    .then((r) => {
      if (!r.ok) throw new Error(`El panel respondió ${r.status}`)
      return r.json()
    })
    .then((foto: Foto) => emitir({ foto, relevando: false }))
    .catch((e) =>
      // El error NO se convierte en una foto vacía: la pantalla tiene que poder
      // decir «no pude relevar» en vez de mostrar todo en cero.
      emitir({ relevando: false, error: e instanceof Error ? e.message : String(e) }),
    )
    .finally(() => {
      pedido = null
    })

  return pedido
}

export function useDia() {
  const [local, setLocal] = useState<Estado>(estado)

  useEffect(() => {
    oyentes.add(setLocal)
    pedirFoto()
    return () => {
      oyentes.delete(setLocal)
    }
  }, [])

  return {
    ...local,
    actualizar: () => pedirFoto(true),
  }
}
