import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, type InputSpec, type Issue, api } from '../api'
import { Graph } from '../components/Graph'

function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return <p className="good-text">✓ Sin errores ni avisos.</p>
  return (
    <ul className="plain issues">
      {issues.map((issue, index) => (
        <li key={index} className={issue.severity}>
          <strong>{issue.severity === 'error' ? 'Error' : 'Aviso'}</strong> <code>{issue.code}</code>{' '}
          {(issue.node || issue.path) && <span className="dim">{[issue.node, issue.path].filter(Boolean).join(' ')}</span>}
          <div>{issue.message}</div>
          {issue.hint && <div className="dim small">→ {issue.hint}</div>}
        </li>
      ))}
    </ul>
  )
}

function LaunchForm({ plan, inputs, valid }: { plan: string; inputs: Record<string, InputSpec>; valid: boolean }) {
  const navigate = useNavigate()
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(inputs).map(([name, spec]) => [name, spec.default != null ? String(spec.default) : ''])),
  )
  const launch = useMutation({
    mutationFn: () => api.createRun(plan, Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ''))),
    onSuccess: (result) => navigate(`/runs/${result.id}`),
  })
  const detail = launch.error instanceof ApiError ? (launch.error.detail as { issues?: Issue[] } | undefined) : undefined

  return (
    <form
      className="card"
      onSubmit={(event) => {
        event.preventDefault()
        launch.mutate()
      }}
    >
      <h2>Lanzar</h2>
      {Object.entries(inputs).map(([name, spec]) => (
        <label key={name} className="field">
          <span>
            <code>{name}</code> {spec.required && <span className="req">*</span>} <span className="dim small">({spec.type})</span>
          </span>
          <input
            value={values[name] ?? ''}
            required={spec.required && spec.default == null}
            placeholder={spec.description}
            onChange={(e) => setValues({ ...values, [name]: e.target.value })}
          />
        </label>
      ))}
      {Object.keys(inputs).length === 0 && <p className="dim">Este plan no necesita inputs.</p>}
      <button className="btn primary" disabled={!valid || launch.isPending}>
        {launch.isPending ? 'Lanzando…' : 'Lanzar run'}
      </button>
      {!valid && <p className="error small">El plan tiene errores: no se puede lanzar.</p>}
      {launch.error && <p className="error">{launch.error.message}</p>}
      {detail?.issues && <IssueList issues={detail.issues} />}
    </form>
  )
}

export function PlanPage() {
  const { name = '' } = useParams()
  const plan = useQuery({ queryKey: ['plan', name], queryFn: () => api.plan(name) })
  if (plan.isLoading) return <div className="page dim">Cargando…</div>
  if (plan.error || !plan.data) return <div className="page error">No se pudo cargar el plan {name}.</div>
  const data = plan.data

  return (
    <div className="page">
      <div className="crumbs">
        <Link to="/plans">Planes</Link> › {data.name}
      </div>
      <h1>{data.name}</h1>
      <p className="dim">{data.plan?.description}</p>
      {data.graph ? <Graph graph={data.graph} height={460} /> : <div className="card error">El plan no se pudo cargar.</div>}
      <div className="two-cols">
        <section className="card">
          <h2>Validación</h2>
          <IssueList issues={data.issues} />
        </section>
        {data.inputs && <LaunchForm plan={data.name} inputs={data.inputs} valid={data.valid} />}
      </div>
    </div>
  )
}
