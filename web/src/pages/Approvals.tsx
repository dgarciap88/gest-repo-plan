import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { ApprovalCard } from '../components/ApprovalCard'

export function Approvals() {
  const [scope, setScope] = useState<'pending' | 'all'>('pending')
  const approvals = useQuery({ queryKey: ['approvals', scope], queryFn: () => api.approvals(scope) })
  const items = [...(approvals.data ?? [])].reverse()

  return (
    <div className="page">
      <div className="toolbar">
        <h1>Aprobaciones</h1>
        <div className="row">
          <button className={`pill ${scope === 'pending' ? 'active' : ''}`} onClick={() => setScope('pending')}>
            Pendientes
          </button>
          <button className={`pill ${scope === 'all' ? 'active' : ''}`} onClick={() => setScope('all')}>
            Historial
          </button>
        </div>
      </div>
      <p className="dim">
        Todo nodo con efecto externo (push, PR…) espera aquí. Revisa la vista previa de lo que se va a publicar antes de aprobar.
      </p>
      {approvals.isLoading && <p className="dim">Cargando…</p>}
      {!approvals.isLoading && items.length === 0 && <div className="card dim">No hay aprobaciones {scope === 'pending' ? 'pendientes' : ''}.</div>}
      {items.map((a) => (
        <ApprovalCard key={a.id} approval={a} />
      ))}
    </div>
  )
}
