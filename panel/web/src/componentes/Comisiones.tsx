/*
  Comisiones: el mapa de entregas, comisión × unidad.

  Es una matriz y no un resumen a propósito. Este proyecto ya probó tres formas
  de resumir el avance —porcentaje, mediana, promedio— y las tres mienten,
  porque el campus no tiene fechas de entrega y no existe un denominador honesto.
  Una matriz no resume: muestra. Y deja ver cosas que ningún agregado deja, como
  un alumno que entregó la unidad 3 sin haber entregado la 1 ni la 2.

  El número está escrito en cada celda. **La intensidad es refuerzo, no
  información**: quien no distinga los tonos lee el mismo dato.

  Una celda vacía dice `—` y no `0` cuando no se pudo relevar. Son opuestos.
*/

import { desambiguar } from '../lib/unidades'
import { useDia, type Comision } from '../lib/dia'
import { FichaComision } from './FichaComision'
import { Foto } from './Foto'

/** Cinco pasos sobre cero. Los valores están verificados en `contraste.py`. */
function nivel(enviados: number | null, max: number): number {
  if (enviados === null) return -1
  if (enviados === 0 || max === 0) return 0
  return Math.min(5, Math.ceil((enviados / max) * 5))
}

export function Comisiones({ onPreguntar }: { onPreguntar: (t: string) => void }) {
  const { foto, relevando, error, actualizar } = useDia()

  if (error)
    return (
      <section className="vista">
        <h1>Comisiones</h1>
        <p className="error">
          No se pudo relevar el campus. No se muestra nada, en vez de mostrar ceros.
        </p>
      </section>
    )

  if (!foto)
    return (
      <section className="vista">
        <h1>Comisiones</h1>
        <p className="sin-dato">Relevando el campus…</p>
      </section>
    )

  // Una matriz por materia: Prog I y Prog IV no comparten unidades, y ponerlas
  // en la misma grilla haría que la columna «U3» significara dos cosas.
  const materias = new Map<string, Comision[]>()
  for (const c of foto.comisiones) {
    materias.set(c.curso, [...(materias.get(c.curso) ?? []), c])
  }

  return (
    <section className="vista">
      <header className="vista__cabecera">
        <h1>Comisiones</h1>
        <Foto
          procedencia={foto.procedencia}
          relevando={relevando}
          onActualizar={actualizar}
        />
      </header>

      {[...materias].map(([curso, coms]) => {
        const unidades = desambiguar(coms[0]?.detalle.map((d) => d.unidad) ?? [])
        const max = Math.max(
          1,
          ...coms.flatMap((c) => c.detalle.map((d) => d.enviados ?? 0)),
        )

        return (
          <div key={curso} className="mapa">
            <h2>{curso}</h2>
            <table className="mapa__tabla">
              <thead>
                <tr>
                  <th />
                  {unidades.map((u, i) => (
                    <th
                      key={i}
                      className="mapa__unidad"
                      title={coms[0]?.detalle[i]?.titulo}
                    >
                      {u}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {coms.map((c) => (
                  <tr key={c.group_id}>
                    <th className="mapa__com">
                      {c.comision}
                      <span className="mapa__padron">
                        {c.participantes ?? '—'}
                      </span>
                    </th>
                    {c.detalle.map((d) => {
                      const n = nivel(d.enviados, max)
                      return (
                        <td key={d.assign_id} className="mapa__celda">
                          <button
                            data-nivel={n}
                            data-pendiente={d.pendientes ? '' : undefined}
                            title={
                              d.enviados === null
                                ? `${d.titulo}\nNo se pudo relevar: ${d.motivo ?? ''}`
                                : `${d.titulo}\n${d.enviados} entregadas` +
                                  (d.pendientes
                                    ? `\n${d.pendientes} esperando corrección`
                                    : '')
                            }
                            onClick={() =>
                              onPreguntar(
                                `Contame cómo viene «${d.titulo}» en ${c.comision} ` +
                                  `(assign ${d.assign_id}, group_id ${c.group_id}).`,
                              )
                            }
                          >
                            {d.enviados === null ? '—' : d.enviados}
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      })}

      <div className="fichas">
        <h2>Alumno por alumno</h2>
        <p className="informe__bajada">
          Entregas, los dos relojes de acceso y los últimos mensajes de cada uno. Se
          consulta al abrir cada comisión, porque cuesta una decena de consultas al
          campus.
        </p>
        {foto.comisiones.map((c) => (
          <FichaComision
            key={c.group_id}
            comision={`${c.comision} · ${c.curso}`}
            courseId={c.course_id}
            groupId={c.group_id}
            onPreguntar={onPreguntar}
          />
        ))}
      </div>

      <div className="leyenda">
        <span className="leyenda__titulo">Cada celda: cuántos entregaron.</span>
        <span className="leyenda__escala">
          {[0, 1, 2, 3, 4, 5].map((n) => (
            <i key={n} data-nivel={n} />
          ))}
        </span>
        <span>menos → más</span>
        <span className="leyenda__aparte">
          <i data-nivel={2} data-pendiente="" /> tiene entregas esperando corrección
        </span>
        <span className="leyenda__aparte">
          <b>—</b> no se pudo relevar (no es cero)
        </span>
      </div>
    </section>
  )
}
