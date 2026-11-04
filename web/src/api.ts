// Cliente de la API de gestplan y tipos de sus respuestas.

export interface RunSummary {
  id: string
  plan_name: string
  status: string
  inputs: Record<string, unknown>
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  parent_run_id: string | null
  parent_node_id: string | null
  cost_usd: number
  progress: { succeeded: number; total: number; active: string[] }
  children_count: number
  pending_approvals: number
  last: { text: string; level: 'good' | 'bad' | 'warn' | 'info' | 'dim'; at: string }
}

export interface GraphNode {
  type: string
  family: string
  side_effect: 'none' | 'local' | 'external'
  description: string
  events: string[]
  max_attempts: number | null
}
export interface GraphEdge {
  from: string
  event: string
  to: string
  back: boolean
}
export interface GraphView {
  nodes: Record<string, GraphNode>
  edges: GraphEdge[]
  layout: Record<string, { x: number; y: number }>
  custom_layout: boolean
}
export interface NodeState {
  status: string
  attempts: number
  last_event: string | null
  unhandled: boolean
  updated_at: string
  last_error: string | null
  cost_usd: number
}
export interface Preview {
  kind: string
  title?: string
  text?: string
  items?: Record<string, string>
  message?: string
}
export interface Approval {
  id: number
  run_id: string
  node_id: string
  attempt: number
  status: string
  covers: string[]
  previews: Record<string, Preview>
  message: string
  comment: string | null
  decided_by: string | null
  created_at: string
  decided_at: string | null
  plan_name?: string
  inputs?: Record<string, unknown>
}
export interface RunDetail {
  run: RunSummary
  plan: { name: string; description: string } & GraphView
  nodes: Record<string, NodeState>
  children: RunSummary[]
  approvals: Approval[]
}
export interface Attempt {
  id: number
  attempt: number
  status: string
  event: string | null
  resolved_config: Record<string, unknown> | null
  outputs: Record<string, unknown> | null
  error: string | null
  cost_usd: number
  started_at: string
  finished_at: string | null
}
export interface Invocation {
  id: number
  attempt: number
  kind: string
  provider: string
  model: string | null
  argv: string[]
  exit_code: number | null
  cost_usd: number
  tokens_in: number | null
  tokens_out: number | null
  cli_version: string | null
  session_id: string | null
  started_at: string
  result_preview: string
}
export interface Execution {
  id: number
  attempt: number
  kind: string
  argv: string[]
  exit_code: number | null
  started_at: string
}
export interface NodeDetail {
  attempts: Attempt[]
  invocations: Invocation[]
  executions: Execution[]
}
export interface InvocationFull extends Invocation {
  prompt: string
  stdout: string
  result: string
}
export interface TimelineRow {
  node_id: string
  attempt: number
  status: string
  event: string | null
  started_at: string
  finished_at: string | null
  cost_usd: number
}
export interface CleanupPreview {
  count: number
  runs_total: number
  runs: { id: string; plan_name: string; status: string; finished_at: string | null }[]
}
export interface Stats {
  runs: Record<string, number>
  pending_approvals: number
  cost_today_usd: number
  interrupted_nodes: number
}
export interface PlanItem {
  name: string
  path: string
  example: boolean
  valid: boolean
  description: string
  node_count: number
  required_inputs: string[]
  errors: number
  warnings: number
}
export interface Issue {
  code: string
  message: string
  severity: 'error' | 'warning'
  node: string | null
  path: string | null
  hint: string | null
}
export interface InputSpec {
  type: string
  required: boolean
  default: unknown
  description: string
  pattern: string | null
}
export interface PlanDetail {
  name: string
  valid: boolean
  plan: { name: string; description: string } | null
  issues: Issue[]
  graph?: GraphView
  inputs?: Record<string, InputSpec>
}
export interface CatalogField {
  name: string
  type: string
  required: boolean
  default: string
  description: string
}
export interface CatalogStructure {
  name: string
  fields: CatalogField[]
}
export interface CatalogNode {
  type: string
  version: number
  family: string
  description: string
  side_effect: string
  events: { name: string; description: string; failure: boolean }[]
  config_fields: CatalogField[]
  config_structures: CatalogStructure[]
  outputs_fields: CatalogField[]
  outputs_structures: CatalogStructure[]
  dynamic_outputs: boolean
  default_timeout_s: number | null
  example: Record<string, unknown>
}
export interface Catalog {
  limits: Record<string, unknown>
  plan_format: { title: string; body: string }[]
  nodes: CatalogNode[]
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message)
  }
}

/** Token de la API local (GP_API_TOKEN): se toma una vez de ?token= y se guarda solo para esta pestaña. */
export function apiToken(): string {
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('token')
    if (fromUrl) {
      sessionStorage.setItem('gp_token', fromUrl)
      window.history.replaceState(null, '', window.location.pathname + window.location.hash)
    }
    return sessionStorage.getItem('gp_token') ?? ''
  } catch {
    return ''
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = apiToken()
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'X-Gestplan': '1',
      ...(token ? { 'X-Gestplan-Token': token } : {}),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    },
  })
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json()).detail
    } catch {
      detail = undefined
    }
    const message = typeof detail === 'string' ? detail : (detail as { message?: string })?.message
    throw new ApiError(message || `${response.status} ${response.statusText}`, response.status, detail)
  }
  const type = response.headers.get('content-type') || ''
  return (type.includes('json') ? response.json() : response.text()) as Promise<T>
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  stats: () => request<Stats>('/stats'),
  runs: (params: { status?: string; q?: string } = {}) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][])
    return request<RunSummary[]>(`/runs?${query}`)
  },
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  cleanupPreview: (olderThanDays: number, statuses: string[]) =>
    request<CleanupPreview>(`/maintenance/runs?older_than_days=${olderThanDays}&statuses=${statuses.join(',')}`),
  cleanup: (olderThanDays: number, statuses: string[]) =>
    post<{ deleted_roots: number; runs: number; folders_failed: number }>('/maintenance/runs/cleanup', {
      older_than_days: olderThanDays,
      statuses,
    }),
  deleteRun: (id: string) => request(`/runs/${id}`, { method: 'DELETE' }),
  timeline: (id: string) => request<TimelineRow[]>(`/runs/${id}/timeline`),
  createRun: (plan: string, inputs: Record<string, string>) => post<{ id: string }>('/runs', { plan, inputs }),
  cancelRun: (id: string) => post(`/runs/${id}/cancel`),
  retryNode: (id: string, node: string) => post(`/runs/${id}/nodes/${node}/retry`),
  node: (id: string, node: string) => request<NodeDetail>(`/runs/${id}/nodes/${node}`),
  files: (id: string, node: string, attempt: number) =>
    request<{ name: string; size: number }[]>(`/runs/${id}/nodes/${node}/attempts/${attempt}/files`),
  file: (id: string, node: string, attempt: number, name: string) =>
    request<{ name: string; text: string }>(`/runs/${id}/nodes/${node}/attempts/${attempt}/files/${name}`),
  invocation: (id: number) => request<InvocationFull>(`/invocations/${id}`),
  approvals: (status = 'pending') => request<Approval[]>(`/approvals?status=${status}`),
  decide: (id: number, decision: 'approved' | 'rejected', comment: string) =>
    post(`/approvals/${id}/decision`, { decision, comment: comment || null }),
  plans: () => request<PlanItem[]>('/plans'),
  plan: (name: string) => request<PlanDetail>(`/plans/${name}`),
  catalog: () => request<Catalog>('/catalog'),
  catalogMarkdown: () => request<string>('/catalog/markdown'),
}
