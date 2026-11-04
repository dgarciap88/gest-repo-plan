import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ApiError, type ConfigParam, api } from '../api'

const SOURCE: Record<ConfigParam['source'], string> = {
  default: 'por defecto',
  file: 'del .env',
  env: 'del entorno del sistema',
}

function Field({ param, draft, error, onChange }: { param: ConfigParam; draft: string | undefined; error?: string; onChange: (v: string | undefined) => void }) {
  const changed = draft !== undefined
  const value = draft ?? param.value
  const common = {
    value,
    onChange: (e: { target: { value: string } }) => onChange(e.target.value === param.value ? undefined : e.target.value),
    placeholder: param.secret ? (param.configured ? '•••••••• (definido; escribe para cambiarlo)' : 'sin definir') : param.example,
  }
  return (
    <div className={`config-field ${changed ? 'changed' : ''}`}>
      <label>
        <span>
          <code>{param.name}</code> {param.restart && <span className="chip warn" title="Se aplica al reiniciar el servidor">reinicio</span>}
        </span>
        <span className="dim small">{param.label}</span>
        {param.kind === 'tier' ? (
          <select {...common}>
            <option value="">(por defecto)</option>
            {['cheap', 'standard', 'premium'].map((tier) => (
              <option key={tier}>{tier}</option>
            ))}
          </select>
        ) : (
          <input {...common} type={param.secret ? 'password' : 'text'} autoComplete="off" inputMode={param.kind === 'int' || param.kind === 'float' ? 'decimal' : undefined} />
        )}
      </label>
      <div className="dim small">
        {param.configured ? `Activo ${SOURCE[param.source]}` : 'Usa el valor por defecto'}
        {!param.secret && param.effective && param.effective !== param.value && <> · en uso: <code>{param.effective}</code></>}
        {param.shadowed && <span className="error"> · el entorno del sistema tiene prioridad sobre el .env</span>}
        {param.configured && (
          <>
            {' · '}
            <button className="link" onClick={() => onChange(changed && draft === '' ? undefined : '')} title="Borra el valor del .env">
              {changed && draft === '' ? 'deshacer borrado' : 'quitar'}
            </button>
          </>
        )}
      </div>
      {error && <div className="error small">{error}</div>}
    </div>
  )
}

export function ConfigPage() {
  const client = useQueryClient()
  const config = useQuery({ queryKey: ['config'], queryFn: api.config })
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState<{ text: string; kind: 'good' | 'warn' } | null>(null)

  const grouped = useMemo(() => {
    const groups: Record<string, ConfigParam[]> = {}
    for (const param of config.data?.params ?? []) (groups[param.group] ??= []).push(param)
    return groups
  }, [config.data])

  const save = useMutation({
    mutationFn: () => api.saveConfig(Object.fromEntries(Object.entries(drafts).map(([name, value]) => [name, value === '' ? null : value]))),
    onSuccess: (result) => {
      setDrafts({})
      setErrors({})
      const restart = result.restart_required.length ? ` Para aplicar ${result.restart_required.join(', ')} hay que reiniciar el servidor.` : ''
      setNotice({ text: `Guardado (${result.changed.length} cambio/s).${restart}`, kind: restart ? 'warn' : 'good' })
      client.invalidateQueries({ queryKey: ['config'] })
      client.invalidateQueries({ queryKey: ['stats'] })
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? (error.detail as { errors?: Record<string, string> } | undefined) : undefined
      setErrors(detail?.errors ?? {})
      setNotice(null)
    },
  })

  const count = Object.keys(drafts).length
  return (
    <div className="page">
      <h1>Configuración</h1>
      <p className="dim">
        Parámetros del servidor, guardados en <code>{config.data?.file ?? '.env'}</code>. Los secretos nunca se muestran. Los marcados con
        «reinicio» se aplican al volver a arrancar el servidor; el resto, al guardar.
      </p>
      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}
      {save.isError && Object.keys(errors).length === 0 && <div className="error">{String(save.error)}</div>}
      {config.isLoading && <p className="dim">Cargando…</p>}
      {Object.entries(config.data?.groups ?? {}).map(([group, title]) => (
        <section className="card" key={group}>
          <h2>{title}</h2>
          <div className="config-grid">
            {(grouped[group] ?? []).map((param) => (
              <Field
                key={param.name}
                param={param}
                draft={drafts[param.name]}
                error={errors[param.name]}
                onChange={(value) =>
                  setDrafts((current) => {
                    const next = { ...current }
                    if (value === undefined) delete next[param.name]
                    else next[param.name] = value
                    return next
                  })
                }
              />
            ))}
          </div>
        </section>
      ))}
      <div className="config-bar">
        <span className="dim">{count ? `${count} cambio(s) sin guardar` : 'Sin cambios'}</span>
        <button className="btn" disabled={!count} onClick={() => { setDrafts({}); setErrors({}) }}>
          Descartar
        </button>
        <button className="btn primary" disabled={!count || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Guardando…' : 'Guardar'}
        </button>
      </div>
    </div>
  )
}
