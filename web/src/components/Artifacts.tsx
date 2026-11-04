import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { type ArtifactItem, api } from '../api'
import { fmtAgo, fmtTime } from '../format'

export const KIND_LABEL: Record<string, string> = {
  pr: 'PR',
  merge: 'Fusión',
  branch: 'Rama',
  issue: 'Ticket',
  subtask: 'Subtarea',
  comment: 'Comentario',
  page: 'Página',
}

export function ArtifactTable({ items, showRun }: { items: ArtifactItem[]; showRun?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Tipo</th>
            <th>Artefacto</th>
            <th>Nodo</th>
            {showRun && <th>Run</th>}
            <th>Creado</th>
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id}>
              <td>
                <span className={`chip kind-${a.kind}`}>{KIND_LABEL[a.kind] ?? a.kind}</span>
              </td>
              <td>
                {a.url ? (
                  <a href={a.url} target="_blank" rel="noreferrer">
                    {a.title} ↗
                  </a>
                ) : (
                  a.title
                )}
                {a.ref && <div className="dim small">{a.ref}</div>}
              </td>
              <td>
                <code>{a.node_id}</code>
                {a.from_child && (
                  <div className="small">
                    <Link to={`/runs/${a.run_id}`} title="Run hijo que lo creó">
                      del run hijo
                    </Link>
                  </div>
                )}
              </td>
              {showRun && (
                <td>
                  {a.run_exists ? (
                    <Link to={`/runs/${a.run_id}`}>{a.run_title || a.run_id}</Link>
                  ) : (
                    <span title="El run se borró; el artefacto sigue existiendo fuera">{a.run_title || a.run_id} (borrado)</span>
                  )}
                  <div className="dim small">{a.plan_name}</div>
                </td>
              )}
              <td title={fmtTime(a.created_at)}>{fmtAgo(a.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Sección del run: lo que él y sus runs hijos han creado fuera de gestplan. */
export function RunArtifacts({ runId }: { runId: string }) {
  const artifacts = useQuery({ queryKey: ['run', runId, 'artifacts'], queryFn: () => api.runArtifacts(runId), refetchInterval: 10000 })
  const items = artifacts.data ?? []
  if (items.length === 0) return null
  return (
    <section className="card">
      <h2>Artefactos ({items.length})</h2>
      <p className="dim small">Lo que este run ha creado fuera de gestplan.</p>
      <ArtifactTable items={items} />
    </section>
  )
}
