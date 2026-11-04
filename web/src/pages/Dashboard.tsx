import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { type RunSummary, api } from '../api'
import { CleanupPanel } from '../components/CleanupPanel'
import { StatusBadge } from '../components/StatusBadge'
import { fmtAgo, fmtCost, fmtDuration, fmtInputs, fmtTime } from '../format'

const FILTERS: [string, string][] = [
  ['', 'Todos'],
  ['running', 'En ejecución'],
  ['waiting', 'Esperando'],
  ['failed', 'Fallidos'],
  ['succeeded', 'Correctos'],
  ['cancelled', 'Cancelados'],
]

type SortKey = 'run' | 'plan' | 'status' | 'progress' | 'last' | 'children' | 'duration' | 'cost' | 'created'

const COLUMNS: [SortKey | null, string][] = [
  ['run', 'Run'],
  ['plan', 'Plan'],
  [null, 'Inputs'],
  ['status', 'Estado'],
  ['progress', 'Progreso'],
  ['last', 'Último estado'],
  ['children', 'Hijos'],
  ['created', 'Creado'],
  ['duration', 'Duración'],
  ['cost', 'Coste'],
]

const time = (value: string | null | undefined) => (value ? new Date(value).getTime() : 0)

const SORT_VALUE: Record<SortKey, (r: RunSummary) => string | number> = {
  run: (r) => (r.title || r.id).toLowerCase(),
  plan: (r) => r.plan_name.toLowerCase(),
  status: (r) => r.status,
  progress: (r) => (r.progress.total ? r.progress.succeeded / r.progress.total : 0),
  last: (r) => time(r.last.at),
  children: (r) => r.children_count,
  duration: (r) => (r.started_at ? (r.finished_at ? time(r.finished_at) : Date.now()) - time(r.started_at) : 0),
  cost: (r) => r.cost_usd,
  created: (r) => time(r.created_at),
}

function RunRow({ run, depth = 0 }: { run: RunSummary; depth?: number }) {
  const [open, setOpen] = useState(false)
  const pct = run.progress.total ? Math.round((run.progress.succeeded / run.progress.total) * 100) : 0
  const children = useQuery({
    queryKey: ['run', run.id],
    queryFn: () => api.run(run.id),
    enabled: open && run.children_count > 0,
  })
  return (
    <>
    <tr className={depth ? 'child-row' : undefined}>
      <td style={depth > 1 ? { paddingLeft: 28 + 20 * (depth - 1) } : undefined}>
        {run.children_count > 0 && (
          <button className="toggle" onClick={() => setOpen(!open)} title="Mostrar los runs hijos">
            {open ? '▾' : '▸'}
          </button>
        )}
        <Link to={`/runs/${run.id}`}>{run.title ? <strong>{run.title}</strong> : run.id}</Link>
        {run.title && <div className="dim small">{run.id}</div>}
      </td>
      <td>{run.plan_name}</td>
      <td className="dim small">{fmtInputs(run.inputs)}</td>
      <td>
        <StatusBadge status={run.status} />
        {run.pending_approvals > 0 && <span className="chip warn">✋ {run.pending_approvals}</span>}
      </td>
      <td>
        <div className="progress" title={`${run.progress.succeeded}/${run.progress.total} nodos correctos`}>
          <div style={{ width: `${pct}%` }} />
        </div>
        <span className="dim small">{run.progress.succeeded}/{run.progress.total}</span>
      </td>
      <td>
        <div className={`last ${run.last.level}`} title={run.last.text}>
          {run.last.text}
        </div>
        <span className="dim small" title={fmtTime(run.last.at)}>
          {fmtAgo(run.last.at)}
        </span>
      </td>
      <td>{run.children_count > 0 ? run.children_count : '—'}</td>
      <td>
        <div title={fmtTime(run.created_at)}>{fmtTime(run.created_at)}</div>
        <span className="dim small">{fmtAgo(run.created_at)}</span>
      </td>
      <td>{fmtDuration(run.started_at, run.finished_at)}</td>
      <td>{fmtCost(run.cost_usd)}</td>
    </tr>
    {open && (children.data?.children ?? []).map((child) => <RunRow key={child.id} run={child} depth={depth + 1} />)}
    </>
  )
}

export function Dashboard() {
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [cleaning, setCleaning] = useState(false)
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'created', dir: 'desc' })
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats })
  const runs = useQuery({ queryKey: ['runs', status, q], queryFn: () => api.runs({ status, q }), refetchInterval: 30000 })
  const attention = (runs.data ?? []).filter((r) => r.pending_approvals > 0 || r.status === 'failed')
  const counts = stats.data?.runs ?? {}
  const sorted = useMemo(() => {
    const value = SORT_VALUE[sort.key]
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...(runs.data ?? [])].sort((a, b) => {
      const x = value(a)
      const y = value(b)
      return (x < y ? -1 : x > y ? 1 : 0) * factor
    })
  }, [runs.data, sort])
  const toggleSort = (key: SortKey) =>
    setSort((current) => ({ key, dir: current.key === key && current.dir === 'desc' ? 'asc' : 'desc' }))

  return (
    <div className="page">
      <h1>Dashboard</h1>
      <section className="cards">
        <div className="stat">
          <span className="num">{(counts.running ?? 0) + (counts.queued ?? 0)}</span>
          <span>En ejecución</span>
        </div>
        <Link className="stat warn" to="/approvals">
          <span className="num">{stats.data?.pending_approvals ?? 0}</span>
          <span>Esperando aprobación</span>
        </Link>
        <div className="stat bad">
          <span className="num">{(counts.failed ?? 0) + (stats.data?.interrupted_nodes ?? 0)}</span>
          <span>Fallidos o interrumpidos</span>
        </div>
        <div className="stat good">
          <span className="num">{counts.succeeded ?? 0}</span>
          <span>Correctos</span>
        </div>
        <div className="stat">
          <span className="num">{fmtCost(stats.data?.cost_today_usd ?? 0)}</span>
          <span>
            Coste de hoy{stats.data?.daily_budget_usd ? ` (límite ${fmtCost(stats.data.daily_budget_usd)})` : ''}
          </span>
        </div>
      </section>

      {attention.length > 0 && (
        <section className="card">
          <h2>Requiere atención</h2>
          <ul className="plain">
            {attention.map((r) => (
              <li key={r.id}>
                <StatusBadge status={r.status === 'failed' ? 'failed' : 'waiting_approval'} compact />{' '}
                <Link to={`/runs/${r.id}`}>{r.title || r.plan_name}</Link> <span className="dim small">{fmtInputs(r.inputs)}</span>
                {r.pending_approvals > 0 && <span className="chip warn">{r.pending_approvals} aprobación(es) pendiente(s)</span>}
                {r.error && <span className="error small"> · {r.error}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {cleaning && <CleanupPanel onClose={() => setCleaning(false)} />}

      <section className="card">
        <div className="toolbar">
          <h2>Runs</h2>
          <div className="row">
            {FILTERS.map(([value, label]) => (
              <button key={value} className={`pill ${status === value ? 'active' : ''}`} onClick={() => setStatus(value)}>
                {label}
              </button>
            ))}
            <input placeholder="Buscar por título, id o input…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn small" onClick={() => setCleaning(!cleaning)} title="Borrar runs terminados">
              🧹 Limpiar…
            </button>
          </div>
        </div>
        {runs.isLoading ? (
          <p className="dim">Cargando…</p>
        ) : (runs.data?.length ?? 0) === 0 ? (
          <p className="dim">
            No hay runs. Lanza uno desde <Link to="/plans">Planes</Link>.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {COLUMNS.map(([key, label]) =>
                    key ? (
                      <th key={key} className="sortable" onClick={() => toggleSort(key)} aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                        {label} <span className="dim">{sort.key === key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
                      </th>
                    ) : (
                      <th key={label}>{label}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {sorted.map((run) => (
                  <RunRow key={run.id} run={run} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="dim small">Actualizado {fmtTime(new Date().toISOString())}</p>
      </section>
    </div>
  )
}
