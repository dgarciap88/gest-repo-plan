import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { type CatalogNode, api } from '../api'

const EFFECT: Record<string, string> = { none: 'sin efecto', local: 'local', external: '🔒 externo (exige aprobación)' }

function NodeCard({ node }: { node: CatalogNode }) {
  const props = node.config_schema.properties ?? {}
  const required = new Set(node.config_schema.required ?? [])
  return (
    <details className="card">
      <summary>
        <strong>{node.type}</strong> <span className="chip">{node.family}</span> <span className="dim small">{EFFECT[node.side_effect]}</span>
      </summary>
      <p>{node.description}</p>
      <h4>Eventos</h4>
      <p>
        {node.events.length === 0 && <span className="dim">Dinámicos (según la configuración).</span>}
        {node.events.map((e) => (
          <span key={e.name} className={`chip ${e.failure ? 'bad' : ''}`} title={e.description}>
            {e.name}
          </span>
        ))}
      </p>
      <h4>Configuración</h4>
      <div className="table-wrap">
        <table>
          <tbody>
            {Object.entries(props).map(([name, spec]) => (
              <tr key={name}>
                <td>
                  <code>{name}</code> {required.has(name) && <span className="req">*</span>}
                </td>
                <td className="dim small">{spec.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {Object.keys(node.example).length > 0 && <pre className="code">{JSON.stringify(node.example, null, 2)}</pre>}
    </details>
  )
}

export function CatalogPage() {
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const [filter, setFilter] = useState('')
  const [copied, setCopied] = useState(false)
  const nodes = (catalog.data?.nodes ?? []).filter((n) => `${n.type} ${n.description}`.toLowerCase().includes(filter.toLowerCase()))

  const copy = async () => {
    await navigator.clipboard.writeText(await api.catalogMarkdown())
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="page">
      <div className="toolbar">
        <h1>Catálogo de nodos</h1>
        <div className="row">
          <input placeholder="Filtrar…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <button className="btn" onClick={copy}>
            {copied ? '¡Copiado!' : 'Copiar como Markdown para IA'}
          </button>
        </div>
      </div>
      <p className="dim">Generado del código: es lo que debe leer la IA que diseña planes.</p>
      {catalog.isLoading && <p className="dim">Cargando…</p>}
      {nodes.map((n) => (
        <NodeCard key={n.type} node={n} />
      ))}
    </div>
  )
}
