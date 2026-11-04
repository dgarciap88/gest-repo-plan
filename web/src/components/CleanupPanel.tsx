import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'

const AGES: [number, string][] = [
  [0, 'cualquier antigüedad'],
  [1, 'más de 1 día'],
  [7, 'más de 7 días'],
  [30, 'más de 30 días'],
]
const STATUSES: [string, string][] = [
  ['succeeded', 'Correctos'],
  ['failed', 'Fallidos'],
  ['cancelled', 'Cancelados'],
]

/** Borra runs terminados (con sus logs, auditoría y workspaces). Nunca toca runs en marcha o con aprobaciones pendientes. */
export function CleanupPanel({ onClose }: { onClose: () => void }) {
  const client = useQueryClient()
  const [days, setDays] = useState(7)
  const [statuses, setStatuses] = useState<string[]>(['succeeded', 'cancelled'])
  const preview = useQuery({
    queryKey: ['cleanup', days, statuses],
    queryFn: () => api.cleanupPreview(days, statuses),
    enabled: statuses.length > 0,
  })
  const clean = useMutation({
    mutationFn: () => api.cleanup(days, statuses),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['runs'] })
      client.invalidateQueries({ queryKey: ['stats'] })
      client.invalidateQueries({ queryKey: ['cleanup'] })
    },
  })
  const toggle = (status: string) =>
    setStatuses((current) => (current.includes(status) ? current.filter((s) => s !== status) : [...current, status]))
  const count = preview.data?.count ?? 0

  return (
    <section className="card cleanup">
      <div className="toolbar">
        <h2>Limpiar runs antiguos</h2>
        <button className="btn small" onClick={onClose}>
          Cerrar
        </button>
      </div>
      <p className="dim small">
        Borra de forma definitiva los runs terminados, junto con sus hijos, logs, auditoría y workspaces. No afecta a los runs en curso ni a los que tienen aprobaciones
        pendientes.
      </p>
      <div className="row">
        <label>
          Terminados hace{' '}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {AGES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {STATUSES.map(([value, label]) => (
          <label key={value}>
            <input type="checkbox" checked={statuses.includes(value)} onChange={() => toggle(value)} /> {label}
          </label>
        ))}
      </div>
      <p>
        {statuses.length === 0
          ? 'Elige al menos un estado.'
          : preview.isLoading
            ? 'Calculando…'
            : count === 0
              ? 'No hay runs que cumplan ese criterio.'
              : `Se borrarán ${count} run(s) raíz (${preview.data?.runs_total} contando los hijos).`}
      </p>
      {preview.data && count > 0 && (
        <ul className="plain small dim">
          {preview.data.runs.slice(0, 5).map((r) => (
            <li key={r.id}>
              {r.title ? `${r.title} · ` : ''}{r.id} · {r.plan_name} · {r.status}
            </li>
          ))}
          {count > 5 && <li>… y {count - 5} más</li>}
        </ul>
      )}
      <div className="row">
        <button
          className="btn bad"
          disabled={count === 0 || clean.isPending}
          onClick={() => confirm(`¿Borrar definitivamente ${count} run(s)? No se puede deshacer.`) && clean.mutate()}
        >
          {clean.isPending ? 'Borrando…' : `Borrar ${count} run(s)`}
        </button>
        {clean.data && (
          <span className="good">
            Borrados {clean.data.runs} run(s){clean.data.folders_failed > 0 ? ` (${clean.data.folders_failed} carpeta(s) no se pudieron borrar)` : ''}.
          </span>
        )}
        {clean.error && <span className="error">{String(clean.error)}</span>}
      </div>
    </section>
  )
}
