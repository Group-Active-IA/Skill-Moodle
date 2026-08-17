/*
  Historial de conversaciones, en el navegador del tutor.

  Guarda lo que se ve, no la sesión del agente: si el panel se reinicia, el hilo
  sigue estando y se puede releer, pero seguir hablando arranca una sesión nueva.
  La pantalla lo dice cuando pasa; dar a entender que el agente se acuerda de
  algo que no recuerda sería mentir por omisión.

  Vive en `localStorage` porque es información del tutor sobre su propio trabajo:
  no tiene por qué salir de su máquina, y el panel no tiene base de datos propia.
*/

export type Conversacion = {
  id: string
  titulo: string
  at: number
  sesion: string | null
  items: unknown[]
}

const CLAVE = 'campus-navigator:historial'
const TOPE = 30

export function leer(): Conversacion[] {
  try {
    const crudo = localStorage.getItem(CLAVE)
    if (!crudo) return []
    const lista = JSON.parse(crudo) as Conversacion[]
    return Array.isArray(lista) ? lista : []
  } catch {
    // Un historial ilegible no puede romper el panel entero.
    return []
  }
}

export function guardar(conv: Conversacion): Conversacion[] {
  const otras = leer().filter((c) => c.id !== conv.id)
  const lista = [conv, ...otras].slice(0, TOPE)
  try {
    localStorage.setItem(CLAVE, JSON.stringify(lista))
  } catch {
    // Cuota llena: se pierde el guardado, no la conversación en curso.
  }
  return lista
}

export function borrar(id: string): Conversacion[] {
  const lista = leer().filter((c) => c.id !== id)
  localStorage.setItem(CLAVE, JSON.stringify(lista))
  return lista
}

export function titular(texto: string): string {
  const limpio = texto.trim().replace(/\s+/g, ' ')
  return limpio.length > 42 ? `${limpio.slice(0, 42)}…` : limpio
}
