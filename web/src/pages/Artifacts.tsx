import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { ArtifactTable, KIND_LABEL } from '../components/Artifacts'

export function ArtifactsPage() {
  const [kind, setKind] = useState('')
  const [q, setQ] = useState('')
  const artifacts = useQuery({ queryKey: ['artifacts', kind, q], queryFn: () => api.artifacts({ kind, q }), refetchInterval: 30000 })
  const items = artifacts.data ?? []

  return (
    <div className="page">
      <h1>Artefactos</h1>
      <p className="dim">
        Todo lo que los planes han creado fuera de gestplan: PR, tickets, páginas, ramas… Se conservan aunque se borre el run que los
        creó, para saber qué hay que limpiar.
      </p>
      <section className="card">
        <div className="toolbar">
          <div className="row">
            <button className={`pill ${kind === '' ? 'active' : ''}`} onClick={() => setKind('')}>
              Todos
            </button>
            {Object.entries(KIND_LABEL).map(([value, label]) => (
              <button key={value} className={`pill ${kind === value ? 'active' : ''}`} onClick={() => setKind(value)}>
                {label}
              </button>
            ))}
            <input placeholder="Buscar por título, referencia, run o plan…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        {artifacts.isLoading ? (
          <p className="dim">Cargando…</p>
        ) : items.length === 0 ? (
          <p className="dim">Todavía no hay artefactos{kind || q ? ' con ese filtro' : ''}.</p>
        ) : (
          <ArtifactTable items={items} showRun />
        )}
      </section>
    </div>
  )
}
