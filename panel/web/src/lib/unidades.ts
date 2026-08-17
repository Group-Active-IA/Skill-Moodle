/*
  Etiquetas de columna para las grillas de actividades.

  Prog IV tiene DOS actividades de la unidad 1 (Fundamentos Spring Boot y APIs
  REST) y varias que caen en la misma abreviatura. Dos columnas con el mismo
  nombre son dos columnas que el lector no puede distinguir, y peor: parecen la
  misma actividad contada dos veces.

  Se numeran en orden de aparición y el título real viaja siempre en el tooltip.
  Vive acá y no dentro de una vista porque el mapa de Comisiones y las barras de
  Informes tienen que rotular igual: si una dice `U1` y la otra `U1·2` para la
  misma columna, el lector cree que son datos distintos.
*/

/** Hace cuánto es la foto, en castellano. */
export function hace(ts: number): string {
  const seg = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (seg < 90) return 'recién'
  const min = Math.round(seg / 60)
  if (min < 60) return `hace ${min} min`
  return `hace ${Math.round(min / 60)} h`
}

export function desambiguar(etiquetas: string[]): string[] {
  const veces = new Map<string, number>()
  for (const e of etiquetas) veces.set(e, (veces.get(e) ?? 0) + 1)

  const vistas = new Map<string, number>()
  return etiquetas.map((e) => {
    if ((veces.get(e) ?? 0) < 2) return e
    const n = (vistas.get(e) ?? 0) + 1
    vistas.set(e, n)
    return `${e}·${n}`
  })
}
