/*
  Cliente del chat.

  Usa `fetch` con POST y lee el cuerpo como stream, en vez de `EventSource`, que
  sólo hace GET. El turno puede quedar frenado esperando una confirmación: cuando
  llega un evento `confirmacion`, el backend está bloqueado hasta que se conteste
  por `confirmar()`, que es otro request y viaja en paralelo.
*/

export type Evento =
  | { tipo: 'sesion'; id: string }
  | { tipo: 'texto'; delta: string }
  | {
      tipo: 'herramienta'
      tool: string
      propia: boolean
      entrada: Record<string, unknown>
    }
  | { tipo: 'pensando' }
  | {
      tipo: 'confirmacion'
      id: string
      tool: string
      irreversible: boolean
      entrada: Record<string, unknown>
    }
  | { tipo: 'error'; mensaje: string }
  | { tipo: 'fin' }

export async function* conversar(
  texto: string,
  sesion: string | null,
  signal?: AbortSignal,
): AsyncGenerator<Evento> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ texto, sesion }),
    signal,
  })

  if (!res.ok || !res.body) {
    throw new Error(`El panel respondió ${res.status}. ¿Está corriendo el backend?`)
  }

  const lector = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let resto = ''

  while (true) {
    const { done, value } = await lector.read()
    if (done) return
    resto += value

    // Los eventos SSE se separan por línea en blanco. Lo que quede a medias
    // vuelve al buffer: cortar un JSON por la mitad es un error silencioso.
    const partes = resto.split('\n\n')
    resto = partes.pop() ?? ''

    for (const parte of partes) {
      const linea = parte.split('\n').find((l) => l.startsWith('data: '))
      if (!linea) continue
      yield JSON.parse(linea.slice(6)) as Evento
    }
  }
}

export async function confirmar(
  sesion: string,
  id: string,
  ok: boolean,
  entrada?: Record<string, unknown>,
): Promise<void> {
  const res = await fetch(`/api/chat/${sesion}/confirmar`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, ok, entrada }),
  })
  if (!res.ok) {
    const detalle = await res.json().catch(() => ({ detail: 'error desconocido' }))
    throw new Error(detalle.detail)
  }
}

export async function salud(): Promise<{ ok: boolean; version_skill: string }> {
  const res = await fetch('/api/salud')
  return res.json()
}
