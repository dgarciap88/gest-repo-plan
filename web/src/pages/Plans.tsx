import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'

export function Plans() {
  const plans = useQuery({ queryKey: ['plans'], queryFn: api.plans })
  return (
    <div className="page">
      <div className="toolbar">
        <h1>Planes</h1>
        <Link className="btn primary" to="/plans/new">
          + Nuevo plan
        </Link>
      </div>
      <p className="dim">
        Planes JSON de <code>plans/</code> y <code>plans/examples/</code>. Los escribe normalmente una IA; aquí se visualizan, se editan y se lanzan.
      </p>
      {plans.isLoading && <p className="dim">Cargando…</p>}
      <div className="grid-cards">
        {(plans.data ?? []).map((p) => (
          <Link key={p.name} to={`/plans/${p.name}`} className="card plan-card">
            <div className="card-head">
              <strong>{p.name}</strong>
              <span className={`chip ${p.valid ? 'good' : 'bad'}`}>{p.valid ? 'válido' : `${p.errors} error(es)`}</span>
            </div>
            <p className="dim">{p.description || 'Sin descripción'}</p>
            <p className="small dim">
              {p.node_count} nodos · inputs obligatorios: {p.required_inputs.join(', ') || 'ninguno'}
              {p.warnings > 0 && ` · ${p.warnings} aviso(s)`}
              {p.example && ' · ejemplo'}
            </p>
          </Link>
        ))}
      </div>
    </div>
  )
}
