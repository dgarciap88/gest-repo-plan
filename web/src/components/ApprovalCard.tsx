import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { type Approval, type Preview, api } from '../api'
import { fmtInputs, fmtTime } from '../format'
import { StatusBadge } from './StatusBadge'

export function PreviewView({ name, preview }: { name: string; preview: Preview }) {
  return (
    <section className="preview">
      <h4>
        <code>{name}</code> {preview.title && <span className="dim">· {preview.title}</span>}
      </h4>
      {preview.kind === 'keyvalue' && preview.items && (
        <dl className="kv">
          {Object.entries(preview.items).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>
                <pre>{value}</pre>
              </dd>
            </div>
          ))}
        </dl>
      )}
      {preview.kind === 'markdown' && <pre className="preview-text">{preview.text}</pre>}
      {(preview.kind === 'error' || preview.kind === 'none') && <p className="dim">{preview.message}</p>}
    </section>
  )
}

export function ApprovalCard({ approval, showRun = true }: { approval: Approval; showRun?: boolean }) {
  const client = useQueryClient()
  const [comment, setComment] = useState('')
  const decide = useMutation({
    mutationFn: (decision: 'approved' | 'rejected') => api.decide(approval.id, decision, comment),
    onSettled: () => {
      client.invalidateQueries({ queryKey: ['approvals'] })
      client.invalidateQueries({ queryKey: ['run', approval.run_id] })
      client.invalidateQueries({ queryKey: ['stats'] })
    },
  })
  const pending = approval.status === 'pending'

  return (
    <article className="card approval">
      <header className="card-head">
        <div>
          <strong>Aprobación #{approval.id}</strong>{' '}
          {showRun && (
            <>
              · <Link to={`/runs/${approval.run_id}`}>{approval.plan_name || approval.run_id}</Link>{' '}
              <span className="dim">{approval.inputs && fmtInputs(approval.inputs)}</span>
            </>
          )}
        </div>
        <div className="row">
          <span className="dim">{fmtTime(approval.created_at)}</span>
          <StatusBadge status={approval.status === 'pending' ? 'waiting_approval' : approval.status === 'approved' ? 'succeeded' : 'failed'} />
        </div>
      </header>
      {approval.message && <p className="callout">{approval.message}</p>}
      <p className="dim">
        Cubre: {approval.covers.map((c) => <code key={c}>{c} </code>)}
      </p>
      {approval.covers.map((name) => approval.previews[name] && <PreviewView key={name} name={name} preview={approval.previews[name]} />)}
      {pending ? (
        <footer className="decision">
          <textarea placeholder="Comentario (opcional)" value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
          <div className="row">
            <button className="btn good" disabled={decide.isPending} onClick={() => decide.mutate('approved')}>
              Aprobar
            </button>
            <button className="btn bad" disabled={decide.isPending} onClick={() => decide.mutate('rejected')}>
              Rechazar
            </button>
          </div>
          {decide.error && <p className="error">{(decide.error as Error).message}</p>}
        </footer>
      ) : (
        <p className="dim">
          Decidida por {approval.decided_by ?? '—'} el {fmtTime(approval.decided_at)}
          {approval.comment ? ` · «${approval.comment}»` : ''}
        </p>
      )}
    </article>
  )
}
