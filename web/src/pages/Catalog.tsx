import { useQuery } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
import { type CatalogField, type CatalogNode, type CatalogStructure, api } from '../api'

const EFFECT: Record<string, string> = { none: 'sin efecto', local: 'local', external: '🔒 externo (exige aprobación)' }

/** Texto con `código` en línea. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`]+`)/).map((part, i) =>
        part.startsWith('`') && part.endsWith('`') && part.length > 2 ? <code key={i}>{part.slice(1, -1)}</code> : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  )
}

/** Markdown mínimo: párrafos, listas con "- " y bloques de código con ```. */
function Body({ text }: { text: string }) {
  const blocks: JSX.Element[] = []
  const lines = text.split('\n')
  let i = 0
  while (i < lines.length) {
    if (lines[i].startsWith('```')) {
      const code: string[] = []
      for (i++; i < lines.length && !lines[i].startsWith('```'); i++) code.push(lines[i])
      i++
      blocks.push(
        <pre className="code" key={blocks.length}>
          {code.join('\n')}
        </pre>,
      )
    } else if (lines[i].startsWith('- ')) {
      const items: string[] = []
      for (; i < lines.length && lines[i].startsWith('- '); i++) items.push(lines[i].slice(2))
      blocks.push(
        <ul key={blocks.length}>
          {items.map((item, n) => (
            <li key={n}>
              <Inline text={item} />
            </li>
          ))}
        </ul>,
      )
    } else if (!lines[i].trim()) {
      i++
    } else {
      const paragraph: string[] = []
      for (; i < lines.length && lines[i].trim() && !lines[i].startsWith('```') && !lines[i].startsWith('- '); i++) paragraph.push(lines[i])
      blocks.push(
        <p key={blocks.length}>
          <Inline text={paragraph.join(' ')} />
        </p>,
      )
    }
  }
  return <>{blocks}</>
}

function FieldTable({ fields }: { fields: CatalogField[] }) {
  if (fields.length === 0) return <p className="dim small">Sin campos.</p>
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Campo</th>
            <th>Tipo</th>
            <th>Oblig.</th>
            <th>Por defecto</th>
            <th>Descripción</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((f) => (
            <tr key={f.name}>
              <td>
                <code>{f.name}</code>
              </td>
              <td className="small">{f.type}</td>
              <td>{f.required ? <span className="req">sí</span> : <span className="dim">no</span>}</td>
              <td className="small">{f.default && <code>{f.default}</code>}</td>
              <td className="small">{f.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Structures({ items }: { items: CatalogStructure[] }) {
  return (
    <>
      {items.map((s) => (
        <div key={s.name}>
          <h5>
            Estructura <code>{s.name}</code>
          </h5>
          <FieldTable fields={s.fields} />
        </div>
      ))}
    </>
  )
}

function NodeCard({ node }: { node: CatalogNode }) {
  return (
    <details className="card" id={node.type}>
      <summary>
        <strong>{node.type}</strong> <span className="chip">{node.family}</span> <span className="dim small">{EFFECT[node.side_effect]}</span>
      </summary>
      <p>{node.description}</p>
      {node.default_timeout_s && <p className="dim small">Timeout por defecto: {node.default_timeout_s} s</p>}
      <h4>Eventos</h4>
      {node.events.length === 0 ? (
        <p className="dim">Los define la configuración del nodo (explicado en su descripción).</p>
      ) : (
        <ul>
          {node.events.map((e) => (
            <li key={e.name}>
              <span className={`chip ${e.failure ? 'bad' : ''}`}>{e.name}</span> {e.failure && <span className="dim small">(fallo) </span>}
              {e.description}
            </li>
          ))}
        </ul>
      )}
      <h4>Configuración</h4>
      <FieldTable fields={node.config_fields} />
      <Structures items={node.config_structures} />
      <h4>Salidas</h4>
      {node.dynamic_outputs ? (
        <p className="dim">
          Las define el propio script: sus claves quedan disponibles en <code>{'${nodes.<id>.outputs.<clave>}'}</code>.
        </p>
      ) : (
        <>
          <FieldTable fields={node.outputs_fields} />
          <Structures items={node.outputs_structures} />
        </>
      )}
      {Object.keys(node.example).length > 0 && (
        <>
          <h4>
            Ejemplo de <code>config</code>
          </h4>
          <pre className="code">{JSON.stringify(node.example, null, 2)}</pre>
        </>
      )}
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
        <h1>Catálogo</h1>
        <div className="row">
          <input placeholder="Filtrar nodos…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <button className="btn" onClick={copy}>
            {copied ? '¡Copiado!' : 'Copiar como Markdown para IA'}
          </button>
        </div>
      </div>
      <p className="dim">
        Referencia completa para escribir planes: el formato y las reglas, y debajo cada tipo de nodo. Se genera del código y es lo que debe leer la IA que
        diseña planes.
      </p>
      {catalog.isLoading && <p className="dim">Cargando…</p>}
      {(catalog.data?.plan_format ?? []).map((section, i) => (
        <details className="card" key={section.title} open={i === 0}>
          <summary>
            <strong>{section.title}</strong>
          </summary>
          <Body text={section.body} />
        </details>
      ))}
      <h2>Nodos ({nodes.length})</h2>
      {nodes.map((n) => (
        <NodeCard key={n.type} node={n} />
      ))}
    </div>
  )
}
