import type { Conversacion } from '../lib/historial'

type Props = {
  vista: string
  onVista: (v: string) => void
  onNueva: () => void
  tema: 'claro' | 'oscuro'
  onTema: () => void
  version: string | null
  historial: Conversacion[]
  actual: string | null
  onAbrir: (id: string) => void
  onBorrar: (id: string) => void
}

const VISTAS = [
  { id: 'chat', nombre: 'Conversación' },
  { id: 'informes', nombre: 'Informes' },
  { id: 'comisiones', nombre: 'Comisiones' },
]

export function Lateral({
  vista,
  onVista,
  onNueva,
  tema,
  onTema,
  version,
  historial,
  actual,
  onAbrir,
  onBorrar,
}: Props) {
  return (
    <aside className="lateral">
      <div className="marca">
        <span>✳</span> Campus Navigator
      </div>

      <button className="nueva" onClick={onNueva}>
        Nueva conversación
      </button>

      <nav className="nav">
        {VISTAS.map((v) => (
          <button
            key={v.id}
            aria-current={vista === v.id ? 'page' : undefined}
            onClick={() => onVista(v.id)}
          >
            {v.nombre}
          </button>
        ))}
      </nav>

      {historial.length > 0 && (
        <div className="recientes">
          <p className="rotulo">Recientes</p>
          {historial.map((c) => (
            <div
              key={c.id}
              className="reciente"
              data-actual={c.id === actual ? '' : undefined}
            >
              <button className="reciente__abrir" onClick={() => onAbrir(c.id)}>
                {c.titulo}
              </button>
              <button
                className="reciente__borrar"
                onClick={() => onBorrar(c.id)}
                aria-label={`Borrar «${c.titulo}»`}
                title="Borrar"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="pie">
        <button className="tema" onClick={onTema}>
          {tema === 'claro' ? 'Modo oscuro' : 'Modo claro'}
        </button>

        <div className="estado-conexion">
          <span className="punto" data-estado={version ? 'ok' : 'sin-conexion'} />
          {version ? `skill v${version}` : 'sin conexión con el panel'}
        </div>
      </div>
    </aside>
  )
}
