import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { type Approval, type GraphNode, type NodeState, api } from '../api'
import { fmtCost, fmtDuration, fmtTime, pretty } from '../format'
import { ApprovalCard } from './ApprovalCard'
import { StatusBadge } from './StatusBadge'

type Tab = 'resumen' | 'datos' | 'logs' | 'ia' | 'aprobacion'
const RETRYABLE = ['failed', 'cancelled', 'interrupted']

interface Props {
  runId: string
  nodeId: string
  meta: GraphNode
  state: NodeState
  approvals: Approval[]
}

export function NodePanel({ runId, nodeId, meta, state, approvals }: Props) {
  const [tab, setTab] = useState<Tab>('resumen')
  const client = useQueryClient()
  const live = ['running', 'waiting_external'].includes(state.status)
  const detail = useQuery({
    queryKey: ['node', runId, nodeId],
    queryFn: () => api.node(runId, nodeId),
    refetchInterval: live ? 2000 : false,
  })
  const retry = useMutation({
    mutationFn: () => api.retryNode(runId, nodeId),
    onSettled: () => client.invalidateQueries({ queryKey: ['run', runId] }),
  })
  const mine = approvals.filter((a) => a.node_id === nodeId)
  const tabs: [Tab, string][] = [
    ['resumen', 'Resumen'],
    ['datos', 'Datos'],
    ['logs', 'Logs'],
    ...(meta.family === 'ia' ? ([['ia', 'IA']] as [Tab, string][]) : []),
    ...(meta.type === 'manual_approval' ? ([['aprobacion', `Aprobación${mine.length ? ` (${mine.length})` : ''}`]] as [Tab, string][]) : []),
  ]
  const attempts = detail.data?.attempts ?? []
  const lastAttempt = attempts.at(-1)?.attempt ?? 1

  return (
    <aside className="panel">
      <header className="panel-head">
        <div>
          <h3>{nodeId}</h3>
          <span className="dim">
            {meta.type}
            {meta.side_effect === 'external' && ' · 🔒 efecto externo'}
          </span>
        </div>
        <StatusBadge status={state.status} />
      </header>
      {RETRYABLE.includes(state.status) && (
        <button className="btn" disabled={retry.isPending} onClick={() => retry.mutate()}>
          Reactivar nodo
        </button>
      )}
      {retry.error && <p className="error">{(retry.error as Error).message}</p>}
      <nav className="tabs">
        {tabs.map(([key, label]) => (
          <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === 'resumen' && (
        <div>
          {meta.description && <p className="dim">{meta.description}</p>}
          <p>
            Último evento: <code>{state.last_event ?? '—'}</code> · Intentos: {state.attempts}
            {meta.max_attempts ? ` (máx. ${meta.max_attempts})` : ''}
          </p>
          {state.last_error && <p className="error">{state.last_error}</p>}
          <h4>Intentos</h4>
          <ul className="timeline">
            {attempts.map((a) => (
              <li key={a.id}>
                <StatusBadge compact status={a.status === 'waiting' ? 'waiting_external' : a.status} />
                <strong>#{a.attempt}</strong> {a.event && <code>{a.event}</code>}
                <span className="dim">
                  {' '}
                  {fmtDuration(a.started_at, a.finished_at)} · {fmtCost(a.cost_usd)}
                </span>
                {a.error && <div className="error small">{a.error}</div>}
              </li>
            ))}
            {attempts.length === 0 && <li className="dim">Todavía no se ha ejecutado.</li>}
          </ul>
        </div>
      )}

      {tab === 'datos' && <DataTab attempts={attempts} />}
      {tab === 'logs' && <LogsTab runId={runId} nodeId={nodeId} defaultAttempt={lastAttempt} live={live} />}
      {tab === 'ia' && (
        <div>
          {(detail.data?.invocations ?? []).map((inv) => (
            <InvocationView key={inv.id} id={inv.id} />
          ))}
          {(detail.data?.invocations.length ?? 0) === 0 && <p className="dim">Sin invocaciones de IA.</p>}
        </div>
      )}
      {tab === 'aprobacion' && (
        <div>
          {mine.map((a) => (
            <ApprovalCard key={a.id} approval={a} showRun={false} />
          ))}
          {mine.length === 0 && <p className="dim">Aún no se ha pedido ninguna aprobación en este nodo.</p>}
        </div>
      )}
    </aside>
  )
}

function DataTab({ attempts }: { attempts: { attempt: number; resolved_config: unknown; outputs: unknown }[] }) {
  const [selected, setSelected] = useState<number | null>(null)
  const current = attempts.find((a) => a.attempt === (selected ?? attempts.at(-1)?.attempt))
  if (!current) return <p className="dim">Sin datos todavía.</p>
  return (
    <div>
      {attempts.length > 1 && (
        <select value={current.attempt} onChange={(e) => setSelected(Number(e.target.value))}>
          {attempts.map((a) => (
            <option key={a.attempt} value={a.attempt}>
              Intento {a.attempt}
            </option>
          ))}
        </select>
      )}
      <h4>Configuración resuelta</h4>
      <pre className="code">{pretty(current.resolved_config)}</pre>
      <h4>Salidas</h4>
      <pre className="code">{pretty(current.outputs)}</pre>
    </div>
  )
}

function LogsTab({ runId, nodeId, defaultAttempt, live }: { runId: string; nodeId: string; defaultAttempt: number; live: boolean }) {
  const [attempt, setAttempt] = useState(defaultAttempt)
  const [name, setName] = useState<string | null>(null)
  useEffect(() => setAttempt(defaultAttempt), [defaultAttempt])
  const files = useQuery({
    queryKey: ['files', runId, nodeId, attempt],
    queryFn: () => api.files(runId, nodeId, attempt),
    refetchInterval: live ? 2000 : false,
  })
  const chosen = name && files.data?.some((f) => f.name === name) ? name : (files.data?.find((f) => f.name === 'node.log') ?? files.data?.[0])?.name
  const text = useQuery({
    queryKey: ['file', runId, nodeId, attempt, chosen],
    queryFn: () => api.file(runId, nodeId, attempt, chosen!),
    enabled: Boolean(chosen),
    refetchInterval: live ? 1500 : false,
  })
  return (
    <div>
      <div className="row">
        <select value={attempt} onChange={(e) => setAttempt(Number(e.target.value))}>
          {Array.from({ length: defaultAttempt }, (_, i) => i + 1).map((n) => (
            <option key={n} value={n}>
              Intento {n}
            </option>
          ))}
        </select>
        <select value={chosen ?? ''} onChange={(e) => setName(e.target.value)}>
          {(files.data ?? []).map((f) => (
            <option key={f.name} value={f.name}>
              {f.name} ({f.size} B)
            </option>
          ))}
        </select>
      </div>
      {(files.data?.length ?? 0) === 0 ? <p className="dim">Sin ficheros de log para este intento.</p> : <pre className="code logs">{text.data?.text}</pre>}
    </div>
  )
}

function InvocationView({ id }: { id: number }) {
  const [open, setOpen] = useState(false)
  const full = useQuery({ queryKey: ['invocation', id], queryFn: () => api.invocation(id), enabled: open })
  const data = full.data
  return (
    <details className="card inv" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <strong>{data?.kind ?? 'invocación'}</strong> #{id}
        {data && (
          <span className="dim">
            {' '}
            · {data.model} · {fmtCost(data.cost_usd)} · {data.tokens_in ?? 0}/{data.tokens_out ?? 0} tokens · {fmtTime(data.started_at)}
          </span>
        )}
      </summary>
      {data && (
        <div>
          <h4>Comando</h4>
          <pre className="code">{data.argv.join(' ')}</pre>
          <p className="dim">
            CLI {data.cli_version ?? '?'} · sesión {data.session_id ?? '—'} · código de salida {data.exit_code}
          </p>
          <h4>Prompt enviado</h4>
          <pre className="code">{data.prompt}</pre>
          <h4>Respuesta</h4>
          <pre className="code">{data.result || data.stdout}</pre>
        </div>
      )}
    </details>
  )
}
