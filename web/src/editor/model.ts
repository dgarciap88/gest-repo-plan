// Modelo del editor de planes: operaciones puras sobre el JSON del plan (sin React).
import type { CatalogNode } from '../api'

/* eslint-disable @typescript-eslint/no-explicit-any */
export type PlanJson = {
  schema_version?: number
  name: string
  description?: string
  inputs?: Record<string, any>
  settings?: Record<string, any>
  nodes: Record<string, { type: string; config?: Record<string, any>; join?: string; max_attempts?: number | null; timeout_s?: number | null; description?: string; version?: number }>
  edges: { from: string; to: string }[]
  layout?: Record<string, { x: number; y: number }>
}

export const NEW_PLAN: PlanJson = { schema_version: 1, name: '', description: '', inputs: {}, settings: { language: 'es' }, nodes: {}, edges: [] }

const NODE_ID = /^[a-z][a-z0-9_]*$/

export function validNodeId(id: string, plan: PlanJson): string | null {
  if (!NODE_ID.test(id)) return 'Usa minúsculas, dígitos y _, empezando por letra'
  if (plan.nodes[id]) return 'Ya existe un nodo con ese id'
  return null
}

/** Eventos que puede emitir un nodo: los del catálogo, o los que define su configuración (route). */
export function eventsOf(plan: PlanJson, id: string, catalog: CatalogNode[]): string[] {
  const node = plan.nodes[id]
  const meta = catalog.find((c) => c.type === node?.type)
  if (!meta) return []
  if (meta.events.length > 0) return meta.events.map((e) => e.name)
  if (node.type === 'route') {
    const cases = (node.config?.cases ?? []) as { event?: string }[]
    return [...new Set([...cases.map((c) => c.event ?? ''), String(node.config?.default ?? '')].filter(Boolean))]
  }
  return ['ok', 'failed'] // p. ej. python_script: los eventos los declara el propio script
}

export function addNode(plan: PlanJson, id: string, meta: CatalogNode, at: { x: number; y: number }): PlanJson {
  const config = JSON.parse(JSON.stringify(meta.example ?? {}))
  return {
    ...plan,
    nodes: { ...plan.nodes, [id]: { type: meta.type, config } },
    layout: { ...(plan.layout ?? {}), [id]: at },
  }
}

export function removeNode(plan: PlanJson, id: string): PlanJson {
  const { [id]: _removed, ...nodes } = plan.nodes
  const { [id]: _pos, ...layout } = plan.layout ?? {}
  const edges = plan.edges.filter((e) => e.from.split('.')[0] !== id && e.to !== id)
  return { ...plan, nodes, edges, layout }
}

export function addEdge(plan: PlanJson, from: string, event: string, to: string): PlanJson {
  const source = `${from}.${event}`
  if (plan.edges.some((e) => e.from === source && e.to === to)) return plan
  return { ...plan, edges: [...plan.edges, { from: source, to }] }
}

/** Renombra un nodo: su clave, su posición, las aristas y las referencias `nodes.<id>.` de las configuraciones. */
export function renameNode(plan: PlanJson, from: string, to: string): PlanJson {
  const pattern = new RegExp(String.raw`(^|[^\w])nodes\.${from}\.`, 'g')
  const rewrite = (value: any): any => {
    if (typeof value === 'string') return value.replace(pattern, `$1nodes.${to}.`)
    if (Array.isArray(value)) return value.map(rewrite)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, rewrite(v)]))
    return value
  }
  const rename = (key: string) => (key === from ? to : key)
  const nodes = Object.fromEntries(Object.entries(plan.nodes).map(([id, node]) => [rename(id), { ...node, config: rewrite(node.config) }]))
  const edges = plan.edges.map((e) => {
    const [source, ...event] = e.from.split('.')
    return { from: [rename(source), ...event].join('.'), to: rename(e.to) }
  })
  const layout = plan.layout && Object.fromEntries(Object.entries(plan.layout).map(([id, at]) => [rename(id), at]))
  return { ...plan, nodes, edges, ...(layout ? { layout } : {}) }
}

export function removeEdge(plan: PlanJson, from: string, to: string): PlanJson {
  return { ...plan, edges: plan.edges.filter((e) => !(e.from === from && e.to === to)) }
}

/** Nodos desde los que se puede llegar a `id` (sus salidas son referenciables en su configuración). */
export function upstream(plan: PlanJson, id: string): string[] {
  const parents: Record<string, string[]> = {}
  for (const edge of plan.edges) (parents[edge.to] ??= []).push(edge.from.split('.')[0])
  const seen = new Set<string>()
  const queue = [...(parents[id] ?? [])]
  while (queue.length) {
    const next = queue.pop()!
    if (seen.has(next) || next === id) continue
    seen.add(next)
    queue.push(...(parents[next] ?? []))
  }
  return [...seen].sort()
}

/** Convierte el texto de un campo al tipo que declara el esquema (las referencias ${...} se dejan como texto). */
export function coerce(text: string, type: string | undefined): unknown {
  if (text.startsWith('${')) return text
  if (type === 'integer' && /^-?\d+$/.test(text)) return Number(text)
  if (type === 'number' && /^-?\d+(\.\d+)?$/.test(text)) return Number(text)
  if (type === 'boolean' && (text === 'true' || text === 'false')) return text === 'true'
  return text
}

/** Tipo "principal" de una propiedad de un esquema JSON (primera opción no nula). */
export function schemaType(prop: any): string {
  if (!prop) return 'string'
  if (prop.enum) return 'enum'
  const options: any[] = prop.anyOf ?? prop.oneOf ?? [prop]
  const first = options.find((o) => o.type !== 'null' && !(o.type === 'string' && String(o.pattern ?? '').startsWith('^\\$'))) ?? options[0]
  if (first.$ref) return 'object'
  return first.type ?? 'string'
}

export function sanitize(plan: PlanJson): PlanJson {
  // quita campos vacíos que el backend rellenaría con sus valores por defecto
  const nodes = Object.fromEntries(
    Object.entries(plan.nodes).map(([id, n]) => {
      const clean: any = { ...n }
      for (const key of ['max_attempts', 'timeout_s', 'description'] as const) if (clean[key] === null || clean[key] === '' || clean[key] === undefined) delete clean[key]
      if (clean.join === 'all') delete clean.join
      return [id, clean]
    }),
  )
  return { ...plan, nodes }
}

/** Aristas de retorno (bucles): las que apuntan a un nodo que aún está en la pila de un recorrido en profundidad. */
export function backEdges(plan: PlanJson): Set<string> {
  const out: Record<string, { key: string; to: string }[]> = {}
  for (const edge of plan.edges) (out[edge.from.split('.')[0]] ??= []).push({ key: `${edge.from}->${edge.to}`, to: edge.to })
  const targets = new Set(plan.edges.map((e) => e.to))
  const roots = Object.keys(plan.nodes).filter((id) => !targets.has(id))
  const state: Record<string, 'open' | 'done'> = {}
  const back = new Set<string>()
  const visit = (id: string) => {
    state[id] = 'open'
    for (const edge of out[id] ?? []) {
      if (state[edge.to] === 'open') back.add(edge.key)
      else if (!state[edge.to]) visit(edge.to)
    }
    state[id] = 'done'
  }
  for (const root of roots.length ? roots : Object.keys(plan.nodes).slice(0, 1)) if (!state[root]) visit(root)
  return back
}
