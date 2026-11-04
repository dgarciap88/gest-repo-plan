import { useState } from 'react'
import type { CatalogNode } from '../api'
import { type PlanJson, coerce, schemaType } from './model'

/* eslint-disable @typescript-eslint/no-explicit-any */
const LONG = /^(prompt|body|description|text|summary|subject|message|criteria|content)$/

function asText(value: unknown): string {
  if (value === undefined || value === null) return ''
  return typeof value === 'string' ? value : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

interface Props {
  plan: PlanJson
  id: string
  meta: CatalogNode
  upstreamNodes: string[]
  outputsOf: (nodeId: string) => string[]
  onConfig: (config: Record<string, any>) => void
  onProps: (patch: Record<string, any>) => void
}

/** Formulario de un nodo generado del esquema de su tipo en el catálogo. */
export function NodeForm({ plan, id, meta, upstreamNodes, outputsOf, onConfig, onProps }: Props) {
  const node = plan.nodes[id]
  const config = node.config ?? {}
  const properties = meta.config_schema.properties ?? {}
  const required = new Set(meta.config_schema.required ?? [])
  const [focus, setFocus] = useState<string | null>(null)
  const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({})

  const references = [
    ...Object.keys(plan.inputs ?? {}).map((name) => `\${inputs.${name}}`),
    '${run.id}',
    ...upstreamNodes.flatMap((n) => outputsOf(n).map((field) => `\${nodes.${n}.outputs.${field}}`)),
  ]

  const setField = (name: string, raw: string, type: string) => {
    const next = { ...config }
    if (raw === '') delete next[name]
    else if (type === 'object' || type === 'array') {
      try {
        next[name] = JSON.parse(raw)
        setJsonErrors((current) => ({ ...current, [name]: '' }))
      } catch (error) {
        setJsonErrors((current) => ({ ...current, [name]: String(error) }))
        return
      }
    } else next[name] = coerce(raw, type)
    onConfig(next)
  }

  const insertReference = (reference: string) => {
    if (!focus || !reference) return
    const current = config[focus]
    const text = typeof current === 'string' ? current : ''
    setField(focus, text + reference, 'string')
  }

  return (
    <div className="node-form">
      <p className="dim small">{meta.description}</p>
      <label className="field">
        <span>Descripción del nodo (opcional)</span>
        <input value={node.description ?? ''} onChange={(e) => onProps({ description: e.target.value })} />
      </label>
      <div className="row">
        <label className="field">
          <span>join</span>
          <select value={node.join ?? 'all'} onChange={(e) => onProps({ join: e.target.value })}>
            <option value="all">all (espera a todos los orígenes)</option>
            <option value="any">any (arranca con el primero)</option>
          </select>
        </label>
        <label className="field">
          <span>max_attempts</span>
          <input type="number" min={1} value={node.max_attempts ?? ''} onChange={(e) => onProps({ max_attempts: e.target.value ? Number(e.target.value) : null })} />
        </label>
        <label className="field">
          <span>timeout_s</span>
          <input type="number" min={1} value={node.timeout_s ?? ''} onChange={(e) => onProps({ timeout_s: e.target.value ? Number(e.target.value) : null })} />
        </label>
      </div>

      <h4>Configuración</h4>
      <label className="field">
        <span>Insertar referencia en el campo activo{focus ? ` (${focus})` : ''}</span>
        <select value="" disabled={!focus} onChange={(e) => insertReference(e.target.value)}>
          <option value="">{focus ? 'Elige una referencia…' : 'Haz clic antes en un campo de texto'}</option>
          {references.map((reference) => (
            <option key={reference} value={reference}>
              {reference}
            </option>
          ))}
        </select>
      </label>
      {Object.entries(properties).map(([name, prop]) => {
        const type = schemaType(prop)
        const value = config[name]
        const hint = prop.description as string | undefined
        const label = (
          <span title={hint}>
            <code>{name}</code> {required.has(name) && <span className="req">*</span>}
            {hint && <span className="dim small"> · {hint}</span>}
          </span>
        )
        if (type === 'enum') {
          return (
            <label className="field" key={name}>
              {label}
              <select value={asText(value)} onChange={(e) => setField(name, e.target.value, 'string')}>
                <option value="">(por defecto)</option>
                {(prop.enum as unknown[]).map((option) => (
                  <option key={String(option)} value={String(option)}>
                    {String(option)}
                  </option>
                ))}
              </select>
            </label>
          )
        }
        if (type === 'object' || type === 'array') {
          return (
            <label className="field" key={name}>
              {label}
              <textarea
                className="mono"
                rows={Math.min(10, Math.max(3, asText(value).split('\n').length + 1))}
                defaultValue={asText(value)}
                onFocus={() => setFocus(null)}
                onBlur={(e) => setField(name, e.target.value, type)}
              />
              {jsonErrors[name] && <span className="error small">JSON no válido: {jsonErrors[name]}</span>}
            </label>
          )
        }
        const common = {
          value: asText(value),
          onFocus: () => setFocus(name),
          onChange: (e: { target: { value: string } }) => setField(name, e.target.value, type),
        }
        return (
          <label className="field" key={name}>
            {label}
            {LONG.test(name) || asText(value).includes('\n') ? <textarea rows={4} {...common} /> : <input {...common} placeholder={prop.default !== undefined ? `por defecto: ${JSON.stringify(prop.default)}` : ''} />}
          </label>
        )
      })}
    </div>
  )
}
