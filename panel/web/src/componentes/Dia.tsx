/*
  Lo primero que ve el tutor al abrir el panel.

  Se pinta en dos tiempos a propósito: la grilla de comisiones sale al instante
  del catálogo local, y los pendientes caen cuando termina el relevamiento, que
  son unas cuantas consultas al campus. Esperar treinta segundos frente a una
  pantalla en blanco es peor que ver la estructura y que se complete.

  Mientras no llegó el dato, la celda dice `—` y no `0`. Son cosas distintas y
  este producto tiene documentado lo caro que sale confundirlas.
*/

import { useEffect, useState } from 'react'
import { useDia, type Comision } from '../lib/dia'
import { Foto } from './Foto'

function momento(): string {
  const ahora = new Date()
  const dias = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado']
  // Las dos pasadas del circuito diario: a la mañana importa quién está
  // esperando una respuesta; a la tarde, qué trabajo se acumuló.
  const pasada = ahora.getHours() < 14 ? 'pasada de la mañana' : 'pasada de la tarde'
  return `${dias[ahora.getDay()]} ${ahora.getDate()} · ${pasada}`
}

export function Dia({ onPreguntar }: { onPreguntar: (texto: string) => void }) {
  // La grilla sale del catálogo local (instantáneo) y los números del
  // relevamiento (lento). Se guardan por separado y NO en una sola variable:
  // con la foto en caché el relevamiento contesta primero, y el catálogo —que
  // no trae padrón— borraba los números buenos al llegar.
  const [base, setBase] = useState<Comision[]>([])
  const { foto, relevando: releva, error, actualizar } = useDia()

  const filas = foto?.comisiones ?? base

  // Primer tiempo: la estructura, del catálogo local. Instantáneo.
  useEffect(() => {
    fetch('/api/mis-datos')
      .then((r) => r.json())
      .then((r) => {
        const cursos = r.dato?.datos?.cursos ?? []
        const base: Comision[] = []
        for (const c of cursos) {
          for (const com of c.comisiones_del_tutor ?? []) {
            base.push({
              curso: c.nombre,
              course_id: c.course_id,
              comision: String(com.comision).toUpperCase().replace('COM', 'COM '),
              group_id: com.group_id,
              participantes: null,
              pendientes: 0,
              donde: [],
              fallaron: [],
              degradado: false,
              detalle: [],
            })
          }
        }
        setBase(base)
      })
      .catch(() => setBase([]))
  }, [])

  const relevando = releva || (foto === null && error === null)

  return (
    <section className="dia">
      <header className="dia__cabecera">
        <h1>{momento()}</h1>
        <Foto
          procedencia={foto?.procedencia ?? null}
          relevando={relevando}
          onActualizar={actualizar}
        />
      </header>

      {foto?.procedencia.degradado && (
        <p className="aviso">
          {foto.procedencia.fallaron} de {foto.procedencia.consultas} consultas fallaron. Los
          conteos de abajo están incompletos: un cero acá puede ser «no hay» o «no se
          pudo».
        </p>
      )}

      {error && (
        <p className="error">
          No se pudo relevar el campus. Los padrones y pendientes no se muestran, en vez de
          mostrarse en cero.
        </p>
      )}

      <table className="dia__tabla">
        <thead>
          <tr>
            <th>Comisión</th>
            <th>Materia</th>
            <th className="num">Alumnos</th>
            <th className="num">Pendientes</th>
            <th>Dónde</th>
          </tr>
        </thead>
        <tbody>
          {(filas ?? []).map((f) => (
            <tr
              key={f.group_id}
              onClick={() =>
                onPreguntar(
                  `Mostrame qué hay pendiente en ${f.comision} (curso ${f.course_id}, group_id ${f.group_id}).`,
                )
              }
            >
              <td className="dia__com">{f.comision}</td>
              <td className="dia__curso">{f.curso}</td>
              <td className="num">
                {f.participantes ?? <span className="sin-dato">—</span>}
                {f.invisibles && f.invisibles.length > 0 && (
                  // El padrón de las tareas no coincide con la matrícula. Va acá, al lado
                  // del número que corrige, y no en un cartel arriba: es un matiz del dato,
                  // no una alarma — el trabajo de estos alumnos, si entregaron, ya se
                  // rescata y se cuenta.
                  <span
                    className="dia__extra"
                    title={
                      `La matrícula del grupo tiene ${
                        (f.participantes ?? 0) + f.invisibles.length
                      } alumnos y las tareas listan ${f.participantes}. ` +
                      'Fuera del padrón de tareas:\n' +
                      f.invisibles.map((a) => `  · ${a.nombre}`).join('\n') +
                      '\n\nSi entregaron, su trabajo igual se recupera y entra a la cola. ' +
                      'La causa de que no figuren no se ve por la API: hay que mirarlos en ' +
                      'el campus.'
                    }
                  >
                    {' '}
                    +{f.invisibles.length}
                  </span>
                )}
              </td>
              <td className="num">
                {relevando ? (
                  <span className="sin-dato">—</span>
                ) : f.pendientes > 0 ? (
                  <strong className="dia__pend">{f.pendientes}</strong>
                ) : (
                  0
                )}
              </td>
              <td className="dia__donde">
                {relevando ? (
                  <span className="sin-dato">relevando…</span>
                ) : f.fallaron.length > 0 ? (
                  // Sólo las consultas que fallaron de verdad. Antes esta rama miraba
                  // `degradado`, que también se prende por el padrón, y escribía "0 sin
                  // relevar" — un mensaje que no significa nada y encima tapaba el "al día".
                  <span className="dia__hueco">
                    {f.fallaron.length} sin relevar
                  </span>
                ) : f.donde.length ? (
                  f.donde.map((d) => (
                    <code key={d.titulo} title={d.titulo}>
                      {d.unidad}
                      {d.pendientes > 1 && ` ×${d.pendientes}`}
                    </code>
                  ))
                ) : (
                  <span className="sin-dato">al día</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {foto && !foto.procedencia.degradado && (
        <p className="dia__pie">
          «Al día» significa que las {foto.procedencia.consultas} consultas volvieron sin
          entregas esperando. No incluye el estado «calificado sin nota», que{' '}
          <code>sumario</code> no distingue.
        </p>
      )}
    </section>
  )
}
