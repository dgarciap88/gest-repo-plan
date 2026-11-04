import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { Graph } from '../components/Graph'
import { NodePanel } from '../components/NodePanel'
import { StatusBadge } from '../components/StatusBadge'
import { RunArtifacts } from '../components/Artifacts'
import { Timeline } from '../components/Timeline'
import { fmtAgo, fmtCost, fmtDuration, fmtInputs, fmtTime } from '../format'

export function RunPage() {
  const { id = '' } = useParams()
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const run = useQuery({ queryKey: ['run', id], queryFn: () => api.run(id), refetchInterval: 10000 })
  const cancel = useMutation({
    mutationFn: () => api.cancelRun(id),
    onSettled: () => client.invalidateQueries({ queryKey: ['run', id] }),
  })

  if (run.isLoading) return <div className="page dim">Cargando…</div>
  if (run.error || !run.data) return <div className="page error">No se pudo cargar el run {id}.</div>
  const { run: info, plan, nodes, children, approvals } = run.data
  const pending = approvals.filter((a) => a.status === 'pending')
  const finished = ['succeeded', 'failed', 'cancelled'].includes(info.status)

  return (
    <div className="page">
      <div className="crumbs">
        {info.parent_run_id && (
          <>
            <Link to={`/runs/${info.parent_run_id}`}>↑ run padre</Link> ›{' '}
          </>
        )}
        <Link to="/">Dashboard</Link> › {info.id}
      </div>
      <header className="run-head">
        <div>
          <h1>
            {info.title || info.plan_name} <StatusBadge status={info.status} />
          </h1>
          {info.title && <p className="dim small">Plan {info.plan_name} · {info.id}</p>}
          <p className="dim">{fmtInputs(info.inputs)}</p>
          <p className={`last ${info.last.level}`} style={{ maxWidth: 'none', whiteSpace: 'normal' }}>
            {info.last.text} <span className="dim small">· {fmtAgo(info.last.at)}</span>
          </p>
          <p className="dim small">
            Creado {fmtTime(info.created_at)} · duración {fmtDuration(info.started_at, info.finished_at)} · coste {fmtCost(info.cost_usd)}
          </p>
          {info.error && <p className="error">{info.error}</p>}
        </div>
        <div className="row">
          {!finished && (
            <button className="btn bad" disabled={cancel.isPending} onClick={() => confirm('¿Cancelar este run y sus hijos?') && cancel.mutate()}>
              Cancelar run
            </button>
          )}
        </div>
      </header>

      {pending.length > 0 && (
        <div className="callout warn">
          ✋ {pending.length} aprobación(es) pendiente(s) en{' '}
          {pending.map((a) => (
            <button key={a.id} className="link" onClick={() => setSelected(a.node_id)}>
              {a.node_id}
            </button>
          ))}{' '}
          — o desde <Link to="/approvals">el buzón</Link>.
        </div>
      )}

      <div className="run-grid">
        <Graph graph={plan} states={nodes} selected={selected} onSelect={setSelected} height={560} />
        {selected && nodes[selected] ? (
          <NodePanel runId={id} nodeId={selected} meta={plan.nodes[selected]} state={nodes[selected]} approvals={approvals} />
        ) : (
          <aside className="panel dim">
            <p>Selecciona un nodo del grafo para ver su estado, datos, logs y la actividad de IA.</p>
            <p className="small">
              Las líneas discontinuas son bucles. El color de una arista indica el evento que se disparó; 🔒 marca los nodos con
              efecto externo.
            </p>
          </aside>
        )}
      </div>

      <RunArtifacts runId={id} />

      <Timeline runId={id} onSelect={setSelected} />

      {children.length > 0 && (
        <section className="card">
          <h2>Runs hijos ({children.length})</h2>
          <div className="table-wrap">
            <table>
              <tbody>
                {children.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <Link to={`/runs/${c.id}`}>{c.id}</Link>
                    </td>
                    <td>{c.plan_name}</td>
                    <td className="dim small">{fmtInputs(c.inputs)}</td>
                    <td>
                      <StatusBadge status={c.status} />
                      {c.pending_approvals > 0 && <span className="chip warn">✋ {c.pending_approvals}</span>}
                    </td>
                    <td>
                      {c.progress.succeeded}/{c.progress.total}
                    </td>
                    <td>{fmtCost(c.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
