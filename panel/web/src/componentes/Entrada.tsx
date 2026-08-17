import { useEffect, useRef } from 'react'

type Props = {
  valor: string
  onValor: (v: string) => void
  onEnviar: () => void
  ocupado: boolean
}

export function Entrada({ valor, onValor, onEnviar, ocupado }: Props) {
  const caja = useRef<HTMLTextAreaElement>(null)

  // Foco al cargar: lo primero que hace el tutor es escribir.
  useEffect(() => {
    caja.current?.focus()
  }, [])

  // Crece con el texto sin animar el layout: se mide y se fija la altura.
  useEffect(() => {
    const el = caja.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [valor])

  return (
    <div className="entrada">
      <div className="entrada__caja">
        <textarea
          ref={caja}
          rows={1}
          value={valor}
          placeholder="Escribí tu pedido"
          onChange={(e) => onValor(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              if (!ocupado && valor.trim()) onEnviar()
            }
          }}
        />
        <button
          className="enviar"
          onClick={onEnviar}
          disabled={ocupado || !valor.trim()}
          aria-label="Enviar"
        >
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M8 13V3M8 3L3.5 7.5M8 3l4.5 4.5"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      <p className="pista">Enter envía · Shift+Enter salta de línea</p>
    </div>
  )
}
