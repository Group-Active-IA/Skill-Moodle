/*
  La ficha desplegable de una comisión: alumno por alumno.

  Es la vista que hoy no existe en ningún lado del campus — para armarla a mano
  hay que cruzar la página de participantes, la de cada actividad y la bandeja de
  mensajes. Acá está junta.

  Dos reglas que se ven en el render:

  - `—` cuando no se sabe, nunca `0`. Un alumno con cero entregas porque falló la
    consulta y uno con cero de verdad son personas distintas.
  - El que entra al campus y no abre la materia va marcado, porque con un solo
    reloj parece que está al día.
*/

import { Fragment, useState } from 'react'

type Mensaje = { texto: string | null; de_quien: string | null; timestamp: number | null }

type Alumno = {
  userid: number
  nombre: string
  entregadas: number
  sin_corregir: number
  entregas: { unidad: string; estado: string | null; entregado: boolean; pendiente: boolean }[]
  dias_sin_entrar_al_campus?: number | null
  dias_sin_abrir_la_materia?: number | null
  estado_aula?: string
  desenganchado?: boolean
  para_contactar?: boolean
  mensajes?: Mensaje[]
}

type Ficha = {
  curso: string
  alumnos: Alumno[]
  actividades: { unidad: string; titulo: string }[]
  procedencia: {
    consultas: number
    fallaron: number
    degradado: boolean
    detalle_fallas: { titulo: string; motivo: string }[]
    mensajes?: {
      ok: boolean
      motivo?: string
      conversaciones_en_la_bandeja?: number
      de_esta_comision?: number
      truncada?: boolean
    }
  }
  error?: string
}

function cuando(ts: number | null): string {
  if (!ts) return ''
  const dias = Math.round((Date.now() / 1000 - ts) / 86400)
  if (dias <= 0) return 'hoy'
  if (dias === 1) return 'ayer'
  return `hace ${dias} d`
}

export function FichaComision({
  comision,
  courseId,
  groupId,
  onPreguntar,
}: {
  comision: string
  courseId: number
  groupId: number
  onPreguntar: (t: string) => void
}) {
  const [abierta, setAbierta] = useState(false)
  const [ficha, setFicha] = useState<Ficha | null>(null)
  const [cargando, setCargando] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandido, setExpandido] = useState<number | null>(null)

  async function abrir() {
    setAbierta(!abierta)
    if (ficha || cargando) return
    setCargando(true)
    setError(null)
    try {
      const r = await fetch(`/api/comision?course_id=${courseId}&group_id=${groupId}`)
      const d = await r.json()
      if (d.error) setError(d.error)
      else setFicha(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCargando(false)
    }
  }

  const total = ficha?.actividades.length ?? 0

  return (
    <div className="ficha">
      <button className="ficha__titulo" onClick={abrir} aria-expanded={abierta}>
        <span className="ficha__flecha" data-abierta={abierta ? '' : undefined}>
          ▸
        </span>
        <strong>{comision}</strong>
        <span className="ficha__meta">
          {cargando
            ? 'consultando el campus…'
            : ficha
              ? `${ficha.alumnos.length} alumnos · ${ficha.procedencia.mensajes?.de_esta_comision ?? 0} con hilo de mensajes`
              : 'ver alumno por alumno'}
        </span>
      </button>

      {abierta && (
        <div className="ficha__cuerpo">
          {error && <p className="error">{error}</p>}

          {ficha?.procedencia.mensajes?.truncada && (
            <p className="aviso">
              La bandeja de mensajes vino cortada. «Sin hilo» acá puede significar que
              la conversación no entró en el pedido, no que no exista.
            </p>
          )}

          {ficha?.procedencia.mensajes?.ok === false && (
            <p className="aviso">
              No se pudo leer la bandeja ({ficha.procedencia.mensajes.motivo}). La columna
              de mensajes está vacía por eso, no porque nadie haya escrito.
            </p>
          )}

          {ficha?.procedencia.degradado && (
            <p className="aviso">
              {ficha.procedencia.fallaron} actividad(es) no se pudieron relevar. Los
              conteos de entregas de todas las filas están cortos.
            </p>
          )}

          {ficha && (
            <table className="tabla-simple ficha__tabla">
              <thead>
                <tr>
                  <th>Alumno</th>
                  <th className="num">Entregas</th>
                  <th className="num">Sin corregir</th>
                  <th className="num">Sin abrir la materia</th>
                  <th className="num">Sin entrar al campus</th>
                  <th>Mensajes</th>
                </tr>
              </thead>
              <tbody>
                {ficha.alumnos.map((a) => (
                  <Fragment key={a.userid}>
                    <tr
                      data-contactar={a.para_contactar ? '' : undefined}
                      onClick={() =>
                        setExpandido(expandido === a.userid ? null : a.userid)
                      }
                      className="ficha__fila"
                    >
                      <td>{a.nombre}</td>
                      <td className="num">
                        {a.entregadas}
                        <span className="sin-dato"> / {total}</span>
                      </td>
                      <td className="num">
                        {a.sin_corregir > 0 ? (
                          <strong className="dia__pend">{a.sin_corregir}</strong>
                        ) : (
                          0
                        )}
                      </td>
                      <td className="num">
                        {a.dias_sin_abrir_la_materia ??
                          (a.estado_aula === 'nunca_abrio' ? (
                            <span className="sin-dato">nunca</span>
                          ) : (
                            <span className="sin-dato">—</span>
                          ))}
                      </td>
                      <td className="num">
                        {a.dias_sin_entrar_al_campus ?? <span className="sin-dato">—</span>}
                      </td>
                      <td className="ficha__msg">
                        {a.mensajes?.length ? (
                          <span title="Click en la fila para ver los últimos">
                            {a.mensajes.length}
                            {cuando(a.mensajes.at(-1)?.timestamp ?? null) &&
                              ` · ${cuando(a.mensajes.at(-1)?.timestamp ?? null)}`}
                          </span>
                        ) : (
                          <span className="sin-dato" title="Ni él ni vos abrieron un hilo de mensajes">
                            sin hilo
                          </span>
                        )}
                      </td>
                    </tr>

                    {expandido === a.userid && (
                      <tr className="ficha__detalle">
                        <td colSpan={6}>
                          <div className="ficha__unidades">
                            {a.entregas.map((e, i) => (
                              <code
                                key={i}
                                data-entregado={e.entregado ? '' : undefined}
                                data-pendiente={e.pendiente ? '' : undefined}
                                title={`${e.unidad}: ${e.estado ?? 'sin dato'}`}
                              >
                                {e.unidad}
                              </code>
                            ))}
                          </div>

                          {a.mensajes?.length ? (
                            <div className="ficha__hilo">
                              {a.mensajes.map((m, i) => (
                                <p key={i} data-de={m.de_quien ?? undefined}>
                                  <span className="ficha__quien">
                                    {m.de_quien === 'tutor' ? 'vos' : 'él/ella'}
                                  </span>
                                  <span className="ficha__cuando">
                                    {cuando(m.timestamp)}
                                  </span>
                                  {m.texto}
                                </p>
                              ))}
                            </div>
                          ) : null}

                          <button
                            className="boton boton--secundario"
                            onClick={(ev) => {
                              ev.stopPropagation()
                              onPreguntar(
                                `Contame la situación de ${a.nombre} en ${comision} ` +
                                  `(userid ${a.userid}, curso ${courseId}, group_id ${groupId}).`,
                              )
                            }}
                          >
                            Preguntar por {a.nombre.split(' ')[0]}
                          </button>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
