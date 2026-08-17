/*
  Informes.

  Dos cosas, y ninguna resume nada que no se pueda resumir con honestidad:

  1. **Entregas por unidad**, en barras. Es un conteo, no un porcentaje de
     avance: para un porcentaje haría falta saber cuántas actividades ya
     deberían estar entregadas, y el campus no expone fechas de entrega. Contar
     es honesto; dividir por un denominador inventado, no.

  2. **Quién dejó de abrir la materia**, con los DOS relojes a la vista. El que
     entra al campus todos los días para otra materia y hace un mes que no abre
     la propia es el caso más recuperable, y con un solo reloj es invisible.
*/

import { useState } from 'react'
import { desambiguar } from '../lib/unidades'
import { useDia, type Comision } from '../lib/dia'
import { Foto } from './Foto'

// ---------------------------------------------------------------------------
// Barras
// ---------------------------------------------------------------------------

function Barras({ datos, total }: { datos: { unidad: string; n: number }[]; total: number }) {
  const max = Math.max(1, ...datos.map((d) => d.n))
  // Grilla con proporción real y escalado uniforme. Con
  // `preserveAspectRatio="none"` las barras entran perfecto pero el texto sale
  // estirado, porque el SVG deforma los dos ejes por separado.
  const W = 760
  const ALTO = 150
  const PIE = 22
  const paso = W / datos.length

  return (
    <figure className="grafico">
      <svg viewBox={`0 0 ${W} ${ALTO + PIE}`} role="img">
        <title>Entregas por unidad</title>
        {datos.map((d, i) => {
          const h = (d.n / max) * (ALTO - 16)
          const cx = i * paso + paso / 2
          return (
            <g key={d.unidad}>
              {d.n > 0 && (
                <rect
                  x={cx - paso * 0.3}
                  y={ALTO - h}
                  width={paso * 0.6}
                  height={h}
                  rx="4"
                  className="barra"
                />
              )}
              <text x={cx} y={ALTO - h - 6} className="barra__valor" textAnchor="middle">
                {d.n}
              </text>
              <text x={cx} y={ALTO + 15} className="barra__etiqueta" textAnchor="middle">
                {d.unidad}
              </text>
            </g>
          )
        })}
        <line x1="0" y1={ALTO} x2={W} y2={ALTO} className="barra__base" />
      </svg>
      <figcaption>
        {total} entregas en total, contadas con <code>sumario</code>. Es un conteo, no
        un porcentaje de avance: el campus no expone fechas de entrega, así que no hay
        denominador honesto contra el cual dividir.
      </figcaption>
    </figure>
  )
}

// ---------------------------------------------------------------------------
// Desenganche
// ---------------------------------------------------------------------------

/*
  La forma real de la respuesta, mirada en vivo — no supuesta. Los alumnos
  vienen dentro de `comisiones[]`, no en la raíz, y `dias_sin_abrir_la_materia`
  es `null` cuando nunca la abrió: null y 0 son cosas MUY distintas acá.

  No se muestra el mail. El tutor contacta por el sistema de mensajes del campus
  y no lo necesita; la lista con datos de contacto es otra cosa, tiene su propia
  tool y su propio destinatario.
*/
type Alumno = {
  nombre: string
  userid: number
  estado_aula: string
  dias_sin_abrir_la_materia: number | null
  dias_sin_entrar_al_campus: number | null
  desenganchado_de_la_materia: boolean
  entra_al_campus_sin_abrir_la_materia: boolean
  detalle: string
}

function Desenganche({ com }: { com: Comision }) {
  const [datos, setDatos] = useState<any>(null)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function traer() {
    setCargando(true)
    setError(null)
    try {
      const r = await fetch(
        `/api/inactivos?course_id=${com.course_id}&group_id=${com.group_id}`,
      )
      setDatos(await r.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCargando(false)
    }
  }

  const bloque = datos?.dato?.comisiones?.[0]
  const todos: Alumno[] = bloque?.alumnos ?? []
  // Sólo los desenganchados: la lista es para actuar. Y primero los que entran
  // al campus y NO abren la materia — ésos eligieron no entrar, y son los
  // recuperables.
  const lista = todos
    .filter((a) => a.desenganchado_de_la_materia)
    .sort(
      (a, b) =>
        Number(b.entra_al_campus_sin_abrir_la_materia) -
        Number(a.entra_al_campus_sin_abrir_la_materia),
    )

  return (
    <div className="desenganche">
      <div className="desenganche__cabecera">
        <strong>{com.comision}</strong>
        <span className="dia__curso">{com.curso}</span>
        {!datos && (
          <button className="boton boton--secundario" onClick={traer} disabled={cargando}>
            {cargando ? 'Consultando…' : 'Ver quién no abre la materia'}
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {datos && (
        <>
          {bloque?.resumen && <p className="desenganche__resumen">{bloque.resumen}</p>}

          {datos.procedencia?.avisos?.length > 0 && (
            <p className="aviso">{datos.procedencia.avisos.join(' · ')}</p>
          )}

          {lista.length === 0 ? (
            <p className="sin-dato">
              Ningún alumno figura desenganchado sobre {bloque?.alumnos_totales ?? '—'}{' '}
              relevados. Si eso te sorprende, cruzalo contra la página de Participantes
              antes de darlo por bueno.
            </p>
          ) : (
            <>
              <table className="tabla-simple">
                <thead>
                  <tr>
                    <th>Alumno</th>
                    <th>Situación</th>
                    <th className="num">Sin abrir la materia</th>
                    <th className="num">Sin entrar al campus</th>
                  </tr>
                </thead>
                <tbody>
                  {lista.map((a) => (
                    <tr key={a.userid} data-contactar={a.entra_al_campus_sin_abrir_la_materia ? '' : undefined}>
                      <td>{a.nombre}</td>
                      <td className="desenganche__situacion" title={a.detalle}>
                        {a.entra_al_campus_sin_abrir_la_materia
                          ? 'entra al campus, no abre la materia'
                          : a.estado_aula === 'nunca_abrio'
                            ? 'nunca la abrió'
                            : 'sin actividad en la materia'}
                      </td>
                      <td className="num">
                        {a.dias_sin_abrir_la_materia ?? (
                          <span className="sin-dato">nunca</span>
                        )}
                      </td>
                      <td className="num">{a.dias_sin_entrar_al_campus ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="desenganche__criterio">
                {bloque?.criterio?.a_quien_contactar}
              </p>
            </>
          )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------

export function Informes() {
  const { foto, relevando, error, actualizar } = useDia()

  if (error)
    return (
      <section className="vista">
        <h1>Informes</h1>
        <p className="error">No se pudo relevar el campus.</p>
      </section>
    )

  if (!foto)
    return (
      <section className="vista">
        <h1>Informes</h1>
        <p className="sin-dato">Relevando el campus…</p>
      </section>
    )

  const materias = new Map<string, Comision[]>()
  for (const c of foto.comisiones) {
    materias.set(c.curso, [...(materias.get(c.curso) ?? []), c])
  }

  return (
    <section className="vista">
      <header className="vista__cabecera">
        <h1>Informes</h1>
        <Foto
          procedencia={foto.procedencia}
          relevando={relevando}
          onActualizar={actualizar}
        />
      </header>

      {[...materias].map(([curso, coms]) => {
        // Se suman las comisiones propias de esa materia, no el curso entero:
        // el panel muestra lo que el tutor tiene a cargo.
        // Las mismas etiquetas que el mapa de Comisiones: si una vista dice
        // `U1` y la otra `U1·2` para la misma columna, parecen datos distintos.
        const unidades = desambiguar(coms[0]?.detalle.map((d) => d.unidad) ?? [])
        const datos = unidades.map((u, i) => ({
          unidad: u,
          n: coms.reduce((acc, c) => acc + (c.detalle[i]?.enviados ?? 0), 0),
        }))
        const total = datos.reduce((a, d) => a + d.n, 0)

        return (
          <div key={curso} className="informe">
            <h2>{curso}</h2>
            <p className="informe__bajada">
              Entregas por unidad, sumando {coms.map((c) => c.comision).join(' y ')}.
            </p>
            <Barras datos={datos} total={total} />
          </div>
        )
      })}

      <div className="informe">
        <h2>Quién no abre la materia</h2>
        <p className="informe__bajada">
          Los dos relojes por separado. El que entra al campus a diario y no abre tu
          materia figura «al día» si se mira el reloj equivocado, y es el más
          recuperable de todos.
        </p>
        {foto.comisiones.map((c) => (
          <Desenganche key={c.group_id} com={c} />
        ))}
      </div>
    </section>
  )
}
