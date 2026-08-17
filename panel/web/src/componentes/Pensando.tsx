/*
  El indicador de que el agente está trabajando.

  Existe porque sin él la pantalla queda muda: mandás el mensaje y no pasa nada
  hasta que llega la primera herramienta, que pueden ser varios segundos. Y el
  silencio no distingue «está pensando» de «se colgó», que es la misma trampa de
  siempre: dos estados opuestos que se ven igual.

  Por eso a partir de los cinco segundos muestra el tiempo. Un relevamiento del
  campus tarda treinta, y saber que van 12 y no 120 es la diferencia entre
  esperar tranquilo y recargar la página.
*/

import { useEffect, useState } from 'react'

export function Pensando() {
  const [seg, setSeg] = useState(0)

  useEffect(() => {
    const t = setInterval(() => setSeg((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="pensando" role="status" aria-live="polite">
      <span className="pensando__puntos" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>Pensando</span>
      {seg >= 5 && <span className="pensando__reloj">{seg}s</span>}
    </div>
  )
}
