import { useEffect, useRef, useState } from 'react'
import { Confirmar } from './componentes/Confirmar'
import { Comisiones } from './componentes/Comisiones'
import { Dia } from './componentes/Dia'
import { Informes } from './componentes/Informes'
import { Entrada } from './componentes/Entrada'
import { Lateral } from './componentes/Lateral'
import { Mensaje } from './componentes/Mensaje'
import { Pensando } from './componentes/Pensando'
import { Traza } from './componentes/Traza'
import { conversar, confirmar, salud, type Evento } from './lib/chat'
import * as hist from './lib/historial'
import type { Conversacion } from './lib/historial'

type Item =
  | { clase: 'tutor'; texto: string }
  | { clase: 'agente'; texto: string }
  | { clase: 'traza'; tools: { tool: string; propia: boolean }[] }
  | { clase: 'error'; texto: string }
  | {
      clase: 'confirmacion'
      id: string
      tool: string
      irreversible: boolean
      entrada: Record<string, unknown>
      resuelta?: 'ok' | 'no'
    }

// Lo que la tabla de arriba NO contesta. Preguntar «qué me falta corregir»
// cuando el pendiente ya está en pantalla sería mandar al tutor a esperar
// treinta segundos por algo que está mirando.
const EJEMPLOS = [
  '¿Hay mensajes o foros sin responder?',
  '¿Quién entra al campus y no abre mi materia?',
  'Mostrame el estado de las altas y bajas de mis comisiones',
]

export default function App() {
  const [vista, setVista] = useState('chat')
  const [items, setItems] = useState<Item[]>([])
  const [borrador, setBorrador] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [sesion, setSesion] = useState<string | null>(null)
  const [version, setVersion] = useState<string | null>(null)
  const [historial, setHistorial] = useState<Conversacion[]>(() => hist.leer())
  const [convId, setConvId] = useState<string | null>(null)
  const [tema, setTema] = useState<'claro' | 'oscuro'>(
    () => (localStorage.getItem('tema') as 'claro' | 'oscuro') ?? 'claro',
  )
  const fondo = useRef<HTMLDivElement>(null)

  useEffect(() => {
    document.documentElement.dataset.tema = tema
    localStorage.setItem('tema', tema)
  }, [tema])

  useEffect(() => {
    salud()
      .then((s) => setVersion(s.version_skill))
      .catch(() => setVersion(null))
  }, [])

  useEffect(() => {
    fondo.current?.scrollTo({ top: fondo.current.scrollHeight })
  }, [items])

  // El hilo se guarda solo. Se guarda al terminar el turno y no en cada delta:
  // escribir en localStorage con cada token es tirar trabajo a la basura.
  useEffect(() => {
    if (ocupado || !convId || items.length === 0) return
    const primero = items.find((i) => i.clase === 'tutor')
    setHistorial(
      hist.guardar({
        id: convId,
        titulo: hist.titular(primero?.clase === 'tutor' ? primero.texto : 'Sin título'),
        at: Date.now(),
        sesion,
        items,
      }),
    )
  }, [ocupado, convId, items, sesion])

  function abrir(id: string) {
    const conv = historial.find((c) => c.id === id)
    if (!conv) return
    // Una confirmación restaurada ya no tiene a nadie esperándola del otro lado:
    // se muestra como caducada en vez de ofrecer un botón que no hace nada.
    const restaurados = (conv.items as Item[]).map((it) =>
      it.clase === 'confirmacion' && !it.resuelta ? { ...it, resuelta: 'no' as const } : it,
    )
    setItems(restaurados)
    setSesion(conv.sesion)
    setConvId(conv.id)
    setVista('chat')
  }

  function nueva() {
    setItems([])
    setSesion(null)
    setConvId(null)
    setVista('chat')
  }

  async function enviar(texto?: string) {
    const pedido = (texto ?? borrador).trim()
    if (!pedido || ocupado) return

    setBorrador('')
    setOcupado(true)
    setItems((prev) => [...prev, { clase: 'tutor', texto: pedido }])
    if (!convId) setConvId(crypto.randomUUID())

    // El id de sesión llega en el primer evento del stream. Como el estado de
    // React no se actualiza a tiempo para el mismo turno, se guarda acá para
    // que la confirmación pueda usarlo enseguida.
    let sesionActual = sesion

    try {
      for await (const ev of conversar(pedido, sesion)) {
        aplicar(ev)
        if (ev.tipo === 'sesion') sesionActual = ev.id
      }
    } catch (e) {
      setItems((prev) => [
        ...prev,
        { clase: 'error', texto: e instanceof Error ? e.message : String(e) },
      ])
    } finally {
      setOcupado(false)
      if (sesionActual !== sesion) setSesion(sesionActual)
    }
  }

  function aplicar(ev: Evento) {
    setItems((prev) => {
      switch (ev.tipo) {
        case 'texto': {
          const ultimo = prev[prev.length - 1]
          if (ultimo?.clase === 'agente') {
            return [...prev.slice(0, -1), { ...ultimo, texto: ultimo.texto + ev.delta }]
          }
          return [...prev, { clase: 'agente', texto: ev.delta }]
        }
        case 'herramienta': {
          // Las consultas seguidas se juntan en un solo bloque: quince líneas de
          // "Consultando X" empujan la respuesta fuera de la pantalla.
          const ultimo = prev[prev.length - 1]
          const paso = { tool: ev.tool, propia: ev.propia }
          if (ultimo?.clase === 'traza') {
            return [...prev.slice(0, -1), { ...ultimo, tools: [...ultimo.tools, paso] }]
          }
          return [...prev, { clase: 'traza', tools: [paso] }]
        }
        case 'confirmacion':
          return [
            ...prev,
            {
              clase: 'confirmacion',
              id: ev.id,
              tool: ev.tool,
              irreversible: ev.irreversible,
              entrada: ev.entrada,
            },
          ]
        case 'error':
          return [...prev, { clase: 'error', texto: ev.mensaje }]
        default:
          return prev
      }
    })
  }

  async function decidir(
    id: string,
    ok: boolean,
    entrada: Record<string, unknown> | undefined,
    sid: string,
  ) {
    setItems((prev) =>
      prev.map((it) =>
        it.clase === 'confirmacion' && it.id === id
          ? { ...it, resuelta: ok ? 'ok' : 'no' }
          : it,
      ),
    )
    try {
      await confirmar(sid, id, ok, entrada)
    } catch (e) {
      setItems((prev) => [
        ...prev,
        { clase: 'error', texto: e instanceof Error ? e.message : String(e) },
      ])
    }
  }

  const vacio = items.length === 0

  return (
    <div className="panel">
      <Lateral
        vista={vista}
        onVista={setVista}
        onNueva={nueva}
        tema={tema}
        onTema={() => setTema(tema === 'claro' ? 'oscuro' : 'claro')}
        version={version}
        historial={historial}
        actual={convId}
        onAbrir={abrir}
        onBorrar={(id) => {
          setHistorial(hist.borrar(id))
          if (id === convId) nueva()
        }}
      />

      <main className="conversacion">
        <div className="barra">Campus Navigator · Corrección</div>

        <div className="hilo" ref={fondo}>
          {/* Con el hilo vacío la pantalla usa el ancho completo para el
              relevamiento del día; en cuanto hay conversación, vuelve a la
              columna de lectura. */}
          <div
            className={
              vista !== 'chat' || vacio ? 'hilo-ancho' : 'hilo-interior'
            }
          >
            {vacio && vista === 'chat' && (
              <>
                <Dia onPreguntar={(t) => enviar(t)} />
                <div className="ejemplos">
                  {EJEMPLOS.map((e) => (
                    <button key={e} onClick={() => enviar(e)}>
                      {e}
                    </button>
                  ))}
                </div>
              </>
            )}

            {vista === 'informes' && <Informes />}
            {vista === 'comisiones' && (
              <Comisiones
                onPreguntar={(t) => {
                  setVista('chat')
                  enviar(t)
                }}
              />
            )}

            {vista === 'chat' &&
              items.map((it, i) => {
                if (it.clase === 'tutor')
                  return (
                    <div key={i} className="mensaje mensaje--tutor">
                      {it.texto}
                    </div>
                  )
                if (it.clase === 'agente') return <Mensaje key={i} texto={it.texto} />
                if (it.clase === 'traza')
                  return (
                    <div key={i} className="mensaje">
                      <Traza
                        tools={it.tools}
                        activo={ocupado && i === items.length - 1}
                      />
                    </div>
                  )
                if (it.clase === 'error')
                  return (
                    <div key={i} className="mensaje error">
                      {it.texto}
                    </div>
                  )
                if (it.resuelta)
                  return (
                    <div key={i} className="mensaje traza">
                      {it.resuelta === 'ok' ? 'Confirmado' : 'Cancelado'} ·{' '}
                      <code>{it.tool}</code>
                    </div>
                  )
                return (
                  <div key={i} className="mensaje">
                    <Confirmar
                      tool={it.tool}
                      irreversible={it.irreversible}
                      entrada={it.entrada}
                      onDecidir={(ok, entrada) =>
                        sesion && decidir(it.id, ok, entrada, sesion)
                      }
                    />
                  </div>
                )
              })}

            {/* Mientras trabaja, la pantalla nunca queda muda. La traza activa
                ya late sola, así que ahí no se duplica; en todo otro momento
                (recién mandado, o entre un párrafo y la herramienta siguiente)
                va este. */}
            {vista === 'chat' &&
              ocupado &&
              items[items.length - 1]?.clase !== 'traza' &&
              items[items.length - 1]?.clase !== 'confirmacion' && <Pensando />}
          </div>
        </div>

        {vista === 'chat' && (
          <Entrada
            valor={borrador}
            onValor={setBorrador}
            onEnviar={() => enviar()}
            ocupado={ocupado}
          />
        )}
      </main>
    </div>
  )
}
