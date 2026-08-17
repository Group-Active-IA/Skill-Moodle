/*
  La traza de herramientas.

  Un tutor no necesita ver que el agente leyó un archivo o buscó una tool: eso es
  maquinaria. Sí necesita ver **qué le preguntó al campus**, porque es lo que
  después le va a permitir cruzar un número contra otra fuente.

  Por eso las consultas al campus se listan con su nombre y las internas se
  cuentan pero no se nombran. Nada se esconde: el total sigue estando a la vista.
*/

type Props = {
  tools: { tool: string; propia: boolean }[]
  activo: boolean
}

export function Traza({ tools, activo }: Props) {
  const propias = tools.filter((t) => t.propia)
  const internas = tools.length - propias.length
  const ultima = tools[tools.length - 1]

  if (activo) {
    return (
      <div className="traza">
        <span className="latido" />
        {ultima?.propia ? (
          <>
            Consultando <code>{ultima.tool}</code>
          </>
        ) : (
          'Trabajando'
        )}
      </div>
    )
  }

  if (propias.length === 0) return null

  // Una tool llamada 44 veces son 44 chips iguales ocupando seis renglones. Se
  // cuenta, que es lo que dice algo: `sumario ×44`.
  const porNombre = new Map<string, number>()
  for (const t of propias) porNombre.set(t.tool, (porNombre.get(t.tool) ?? 0) + 1)

  return (
    <div className="traza traza--cerrada">
      <span>
        {propias.length === 1 ? 'Consultó el campus:' : `${propias.length} consultas al campus:`}
      </span>
      {[...porNombre].map(([nombre, veces]) => (
        <code key={nombre}>
          {nombre}
          {veces > 1 && <span className="traza__veces"> ×{veces}</span>}
        </code>
      ))}
      {internas > 0 && (
        <span className="traza__internas">
          + {internas} {internas === 1 ? 'paso interno' : 'pasos internos'}
        </span>
      )}
    </div>
  )
}
