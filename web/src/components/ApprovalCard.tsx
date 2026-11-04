import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { type Approval, type Overrides, type Preview, api } from '../api'
import { fmtInputs, fmtTime } from '../format'
import { StatusBadge } from './StatusBadge'

const LONG_FIELDS = new Set(['body', 'description', 'text'])
const NEWLINE = String.fromCharCode(10)

export function PreviewView({
  name,
  preview,
  edits,
  onEdit,
}: {
  name: string
  preview: Preview
  edits?: Record<string, string>
  onEdit?: (field: string, value: string) => void
}) {
  const editing = Boolean(onEdit && preview.editable && edits)
  return (
    <section className="preview">
      <h4>
        <code>{name}</code> {preview.title && <span className="dim">· {preview.title}</span>}
      </h4>
      {editing &&
        Object.keys(preview.editable ?? {}).map((field) => (
          <label className="field" key={field}>
            <span>
              {field} <span className="dim small">(editable: se publicará tal como lo dejes)</span>
            </span>
            {LONG_FIELDS.has(field) ? (
              <textarea
                rows={Math.min(14, Math.max(4, (edits?.[field] ?? '').split(NEWLINE).length + 1))}
                value={edits?.[field] ?? ''}
                onChange={(e) => onEdit?.(field, e.target.value)}
              />
            ) : (
              <input value={edits?.[field] ?? ''} onChange={(e) => onEdit?.(field, e.target.value)} />
            )}
          </label>
        ))}
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
      {preview.kind === 'markdown' && !editing && <pre className="preview-text">{preview.text}</pre>}
      {(preview.kind === 'error' || preview.kind === 'none') && <p className="dim">{preview.message}</p>}
    </section>
  )
}

export function ApprovalCard({ approval, showRun = true }: { approval: Approval; showRun?: boolean }) {
  const client = useQueryClient()
  const [comment, setComment] = useState('')
  // textos que la persona puede corregir antes de aprobar (título y cuerpo del PR, comentarios de Jira...)
  const [edits, setEdits] = useState<Overrides>(() =>
    Object.fromEntries(approval.covers.filter((n) => approval.previews[n]?.editable).map((n) => [n, { ...approval.previews[n].editable }])),
  )
  const changed = (): Overrides => {
    const result: Overrides = {}
    for (const [node, fields] of Object.entries(edits)) {
      const original = approval.previews[node]?.editable ?? {}
      const diff = Object.fromEntries(Object.entries(fields).filter(([field, value]) => value !== original[field]))
      if (Object.keys(diff).length > 0) result[node] = diff
    }
    return result
  }
  const decide = useMutation({
    mutationFn: (decision: 'approved' | 'rejected') =>
      api.decide(approval.id, approval.nonce, decision, comment, decision === 'approved' ? changed() : {}),
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
              · <Link to={`/runs/${approval.run_id}`}>{approval.run_title || approval.plan_name || approval.run_id}</Link>{' '}
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
      {approval.covers.map(
        (name) =>
          approval.previews[name] && (
            <PreviewView
              key={name}
              name={name}
              preview={approval.previews[name]}
              edits={pending ? edits[name] : undefined}
              onEdit={pending ? (field, value) => setEdits({ ...edits, [name]: { ...edits[name], [field]: value } }) : undefined}
            />
          ),
      )}
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
