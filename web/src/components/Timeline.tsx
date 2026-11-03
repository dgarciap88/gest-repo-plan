import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { fmtDuration } from '../format'

/** Línea temporal del run: una fila por nodo y una barra por intento, a escala del tiempo total. */
export function Timeline({ runId, onSelect }: { runId: string; onSelect: (node: string) => void }) {
  const rows = useQuery({ queryKey: ['run', runId, 'timeline'], queryFn: () => api.timeline(runId), refetchInterval: 10000 })
  const data = rows.data ?? []
  if (data.length === 0) return null

  const start = Math.min(...data.map((r) => new Date(r.started_at).getTime()))
  const end = Math.max(...data.map((r) => (r.finished_at ? new Date(r.finished_at).getTime() : Date.now())), start + 1000)
  const span = end - start
  const nodes = [...new Set(data.map((r) => r.node_id))]

  return (
    <section className="card">
      <h2>Línea temporal</h2>
      <div className="timeline">
        {nodes.map((node) => (
          <div className="tl-row" key={node}>
            <button className="link tl-name" onClick={() => onSelect(node)}>
              {node}
            </button>
            <div className="tl-track">
              {data
                .filter((r) => r.node_id === node)
                .map((r) => {
                  const from = new Date(r.started_at).getTime()
                  const to = r.finished_at ? new Date(r.finished_at).getTime() : Date.now()
                  return (
                    <div
                      key={r.attempt}
                      className={`tl-bar st-${r.status}`}
                      style={{ left: `${((from - start) / span) * 100}%`, width: `max(4px, ${((to - from) / span) * 100}%)` }}
                      title={`#${r.attempt} ${r.event ?? r.status} · ${fmtDuration(r.started_at, r.finished_at)}`}
                    />
                  )
                })}
            </div>
          </div>
        ))}
      </div>
      <p className="dim small">Duración total {fmtDuration(new Date(start).toISOString(), new Date(end).toISOString())}. Cada barra es un intento.</p>
    </section>
  )
}
