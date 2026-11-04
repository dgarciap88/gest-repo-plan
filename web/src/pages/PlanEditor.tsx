import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Background,
  Controls,
  type Edge,
  MarkerType,
  MiniMap,
  ReactFlow,
  type OnConnect,
  applyNodeChanges,
  type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, type CatalogNode, type Issue, api } from '../api'
import { type PlanNodeType, nodeTypes } from '../components/Graph'
import { NodeForm } from '../editor/NodeForm'
import {
  NEW_PLAN,
  type PlanJson,
  addEdge,
  addNode,
  backEdges,
  eventsOf,
  removeEdge,
  removeNode,
  renameNode,
  sanitize,
  upstream,
  validNodeId,
} from '../editor/model'
import { layoutGraph } from '../layout'

const NO_NODES: CatalogNode[] = []

type Selection = { kind: 'node'; id: string } | { kind: 'edge'; from: string; to: string } | null
type Tab = 'nodo' | 'plan' | 'problemas' | 'json'

function IssueRows({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return <p className="dim">Sin problemas.</p>
  return (
    <ul className="plain small">
      {issues.map((issue, i) => (
        <li key={i} className={issue.severity === 'error' ? 'error' : 'warn-text'}>
          <strong>{issue.severity === 'error' ? 'ERROR' : 'AVISO'}</strong> <code>{issue.code}</code> {issue.node && <code>{issue.node}</code>} {issue.message}
          {issue.hint && <div className="dim">→ {issue.hint}</div>}
        </li>
      ))}
    </ul>
  )
}

export function PlanEditor() {
  const { name } = useParams()
  const isNew = !name
  const navigate = useNavigate()
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: api.catalog })
  const existing = useQuery({ queryKey: ['plan', name], queryFn: () => api.plan(name ?? ''), enabled: !isNew })
  const [plan, setPlan] = useState<PlanJson | null>(isNew ? { ...NEW_PLAN } : null)
  const [selection, setSelection] = useState<Selection>(null)
  const [tab, setTab] = useState<Tab>('nodo')
  const [issues, setIssues] = useState<Issue[]>([])
  const [dirty, setDirty] = useState(false)
  const [connect, setConnect] = useState<{ from: string; to: string } | null>(null)
  const [newType, setNewType] = useState('')
  const [newId, setNewId] = useState('')
  const [rfNodes, setRfNodes] = useState<PlanNodeType[]>([])
  const [jsonText, setJsonText] = useState('')
  const [jsonError, setJsonError] = useState('')

  const nodesCatalog = useMemo(() => catalog.data?.nodes ?? NO_NODES, [catalog.data])

  // al cargar un plan existente se completa su disposición (si no trae posiciones propias)
  useEffect(() => {
    if (isNew || !existing.data?.plan || plan) return
    const loaded = existing.data.plan as PlanJson
    const complete = loaded.layout && Object.keys(loaded.nodes).every((id) => loaded.layout?.[id])
    const layout = complete || !existing.data.graph ? loaded.layout : layoutGraph(existing.data.graph, 'TB').positions
    setPlan({ ...loaded, layout })
  }, [existing.data, isNew, plan])

  const change = (next: PlanJson) => {
    setPlan(next)
    setDirty(true)
  }

  // validación en vivo (con un pequeño retardo para no saturar al escribir)
  useEffect(() => {
    if (!plan || !plan.name || Object.keys(plan.nodes).length === 0) {
      setIssues([])
      return
    }
    const timer = window.setTimeout(() => {
      api.validatePlan(sanitize(plan)).then((result) => setIssues(result.issues)).catch(() => undefined)
    }, 700)
    return () => window.clearTimeout(timer)
  }, [plan])

  useEffect(() => {
    if (tab === 'json' && plan) setJsonText(JSON.stringify(sanitize(plan), null, 2))
  }, [tab, plan])

  const errorsByNode = useMemo(() => {
    const map: Record<string, 'error' | 'warning'> = {}
    for (const issue of issues) if (issue.node) map[issue.node] = issue.severity === 'error' || map[issue.node] === 'error' ? 'error' : 'warning'
    return map
  }, [issues])
  const errorCount = issues.filter((i) => i.severity === 'error').length

  // nodos de React Flow derivados del plan; las posiciones salen de `layout`
  useEffect(() => {
    if (!plan) return
    setRfNodes(
      Object.entries(plan.nodes).map(([id, spec], index) => {
        const meta = nodesCatalog.find((c) => c.type === spec.type)
        return {
          id,
          type: 'plan',
          position: plan.layout?.[id] ?? { x: 0, y: index * 130 },
          data: {
            id,
            type: spec.type,
            sideEffect: meta?.side_effect ?? 'none',
            description: spec.description ?? meta?.description ?? '',
            selected: selection?.kind === 'node' && selection.id === id,
            direction: 'TB',
            issue: errorsByNode[id],
          },
        }
      }),
    )
  }, [plan, nodesCatalog, selection, errorsByNode])

  const rfEdges = useMemo<Edge[]>(() => {
    if (!plan) return []
    const back = backEdges(plan)
    return plan.edges.map((edge) => {
      const key = `${edge.from}->${edge.to}`
      const chosen = selection?.kind === 'edge' && selection.from === edge.from && selection.to === edge.to
      return {
        id: key,
        source: edge.from.split('.')[0],
        target: edge.to,
        label: edge.from.split('.')[1],
        type: 'smoothstep',
        selected: chosen,
        style: { stroke: chosen ? 'var(--accent)' : 'var(--edge)', strokeWidth: chosen ? 3 : 1.5, strokeDasharray: back.has(key) ? '6 4' : undefined },
        labelStyle: { fill: 'var(--text-dim)', fontSize: 11 },
        labelBgStyle: { fill: 'var(--panel)' },
        markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--edge)' },
      }
    })
  }, [plan, selection])

  const save = useMutation({
    mutationFn: () => api.savePlan(plan!.name, sanitize(plan!)),
    onSuccess: (result) => {
      setDirty(false)
      navigate(`/plans/${plan!.name}`, { state: { saved: result.path } })
    },
  })
  const saveIssues = save.error instanceof ApiError ? ((save.error.detail as { issues?: Issue[] } | undefined)?.issues ?? []) : []

  if (!isNew && existing.isLoading) return <div className="page dim">Cargando…</div>
  if (!plan || catalog.isLoading) return <div className="page error">{existing.error ? `No se pudo cargar el plan ${name}.` : 'Cargando…'}</div>

  const onNodesChange = (changes: NodeChange<PlanNodeType>[]) => setRfNodes((nodes) => applyNodeChanges(changes, nodes))
  const onConnect: OnConnect = (connection) => {
    if (connection.source && connection.target && connection.source !== connection.target) setConnect({ from: connection.source, to: connection.target })
  }
  const selectedNode = selection?.kind === 'node' ? plan.nodes[selection.id] : undefined
  const selectedMeta = selectedNode ? nodesCatalog.find((c) => c.type === selectedNode.type) : undefined

  const suggestId = (type: string) => {
    const base = type.split('.').pop()!.replace(/[^a-z0-9]+/g, '_')
    let index = 1
    while (plan.nodes[index === 1 ? base : `${base}${index}`]) index += 1
    return index === 1 ? base : `${base}${index}`
  }
  const add = () => {
    const meta = nodesCatalog.find((c) => c.type === newType)
    if (!meta) return
    const id = newId || suggestId(meta.type)
    const problem = validNodeId(id, plan)
    if (problem) return window.alert(problem)
    const ys = Object.values(plan.layout ?? {}).map((p) => p.y)
    change(addNode(plan, id, meta, { x: 0, y: ys.length ? Math.max(...ys) + 130 : 0 }))
    setSelection({ kind: 'node', id })
    setTab('nodo')
    setNewId('')
  }
  const autoLayout = () => {
    const graph = {
      nodes: Object.fromEntries(Object.keys(plan.nodes).map((id) => [id, {}])),
      edges: plan.edges.map((e) => ({ from: e.from.split('.')[0], event: e.from.split('.')[1], to: e.to, back: backEdges(plan).has(`${e.from}->${e.to}`) })),
      layout: {},
      custom_layout: false,
    }
    change({ ...plan, layout: layoutGraph(graph as never, 'TB').positions })
  }
  const download = () => {
    const blob = new Blob([JSON.stringify(sanitize(plan), null, 2) + String.fromCharCode(10)], { type: 'application/json' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${plan.name || 'plan'}.json`
    link.click()
    URL.revokeObjectURL(link.href)
  }
  const events = connect ? eventsOf(plan, connect.from, nodesCatalog) : []

  return (
    <div className="page editor-page">
      <div className="crumbs">
        <Link to="/plans">Planes</Link> › {isNew ? 'Nuevo plan' : <Link to={`/plans/${name}`}>{name}</Link>} › Editor
      </div>
      <div className="toolbar">
        <h1>{isNew ? 'Nuevo plan' : `Editar ${plan.name}`}</h1>
        <div className="row">
          {errorCount > 0 && <span className="chip bad">{errorCount} error(es)</span>}
          {dirty && <span className="chip warn">sin guardar</span>}
          <button className="btn small" onClick={autoLayout}>
            Ordenar
          </button>
          <button className="btn small" onClick={download}>
            Descargar JSON
          </button>
          <button className="btn primary" disabled={!plan.name || errorCount > 0 || Object.keys(plan.nodes).length === 0 || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Guardando…' : 'Guardar'}
          </button>
        </div>
      </div>
      {save.error && (
        <div className="callout">
          <p className="error">{save.error.message}</p>
          {saveIssues.length > 0 && <IssueRows issues={saveIssues} />}
        </div>
      )}

      <div className="editor">
        <section className="editor-canvas">
          <div className="editor-add">
            <select value={newType} onChange={(e) => { setNewType(e.target.value); setNewId('') }}>
              <option value="">Tipo de nodo…</option>
              {[...new Set(nodesCatalog.map((n) => n.family))].map((family) => (
                <optgroup key={family} label={family}>
                  {nodesCatalog.filter((n) => n.family === family).map((n) => (
                    <option key={n.type} value={n.type}>
                      {n.type}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <input placeholder={newType ? suggestId(newType) : 'id del nodo'} value={newId} onChange={(e) => setNewId(e.target.value)} />
            <button className="btn small" disabled={!newType} onClick={add}>
              + Añadir nodo
            </button>
            <span className="dim small">Arrastra desde la base de un nodo hasta otro para conectar · Supr borra lo seleccionado</span>
          </div>
          <div className="graph" style={{ height: 640 }}>
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.15 }}
              onNodesChange={onNodesChange}
              onConnect={onConnect}
              onNodeClick={(_e, node) => { setSelection({ kind: 'node', id: node.id }); setTab('nodo') }}
              onEdgeClick={(_e, edge) => setSelection({ kind: 'edge', from: edge.id.split('->')[0], to: edge.id.split('->')[1] })}
              onPaneClick={() => setSelection(null)}
              onNodeDragStop={(_e, node) => change({ ...plan, layout: { ...(plan.layout ?? {}), [node.id]: node.position } })}
              onNodesDelete={(deleted) => change(deleted.reduce((current, node) => removeNode(current, node.id), plan))}
              onEdgesDelete={(deleted) => change(deleted.reduce((current, edge) => removeEdge(current, edge.id.split('->')[0], edge.id.split('->')[1]), plan))}
              deleteKeyCode="Delete"
              zoomOnScroll
              maxZoom={2}
              minZoom={0.1}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable nodeColor="var(--edge)" maskColor="rgba(0,0,0,0.18)" style={{ width: 150, height: 96 }} />
            </ReactFlow>
          </div>
        </section>

        <aside className="editor-panel">
          <div className="row tabs">
            {(['nodo', 'plan', 'problemas', 'json'] as Tab[]).map((t) => (
              <button key={t} className={`pill ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
                {t === 'problemas' ? `Problemas (${issues.length})` : t === 'json' ? 'JSON' : t === 'nodo' ? 'Nodo / arista' : 'Plan'}
              </button>
            ))}
          </div>

          {tab === 'nodo' && selection?.kind === 'node' && selectedNode && selectedMeta && (
            <div>
              <div className="toolbar">
                <h3>
                  <code>{selection.id}</code> <span className="dim small">{selectedNode.type}</span>
                </h3>
                <button
                  className="btn small"
                  onClick={() => {
                    const id = window.prompt('Nuevo id del nodo', selection.id)?.trim()
                    if (!id || id === selection.id) return
                    const problem = validNodeId(id, plan)
                    if (problem) return void window.alert(problem)
                    change(renameNode(plan, selection.id, id))
                    setSelection({ kind: 'node', id })
                  }}
                >
                  Renombrar
                </button>
                <button
                  className="btn bad small"
                  onClick={() => {
                    if (window.confirm(`¿Borrar el nodo ${selection.id} y sus aristas?`)) {
                      change(removeNode(plan, selection.id))
                      setSelection(null)
                    }
                  }}
                >
                  Borrar
                </button>
              </div>
              <NodeForm
                key={selection.id}
                plan={plan}
                id={selection.id}
                meta={selectedMeta}
                upstreamNodes={upstream(plan, selection.id)}
                outputsOf={(nodeId) => nodesCatalog.find((c) => c.type === plan.nodes[nodeId]?.type)?.outputs_fields.map((f) => f.name) ?? []}
                onConfig={(config) => change({ ...plan, nodes: { ...plan.nodes, [selection.id]: { ...selectedNode, config } } })}
                onProps={(patch) => change({ ...plan, nodes: { ...plan.nodes, [selection.id]: { ...selectedNode, ...patch } } })}
              />
              <h4>Aristas de salida</h4>
              <ul className="plain small">
                {plan.edges.filter((e) => e.from.split('.')[0] === selection.id).map((e) => (
                  <li key={`${e.from}->${e.to}`}>
                    <code>{e.from.split('.')[1]}</code> → <code>{e.to}</code>{' '}
                    <button className="link" onClick={() => change(removeEdge(plan, e.from, e.to))}>
                      quitar
                    </button>
                  </li>
                ))}
                {plan.edges.every((e) => e.from.split('.')[0] !== selection.id) && <li className="dim">Ninguna: conecta arrastrando desde la base del nodo.</li>}
              </ul>
            </div>
          )}
          {tab === 'nodo' && selection?.kind === 'edge' && (
            <div>
              <h3>Arista</h3>
              <p>
                <code>{selection.from}</code> → <code>{selection.to}</code>
              </p>
              <button className="btn bad small" onClick={() => { change(removeEdge(plan, selection.from, selection.to)); setSelection(null) }}>
                Borrar arista
              </button>
            </div>
          )}
          {tab === 'nodo' && !selection && <p className="dim">Selecciona un nodo o una arista del grafo, o añade un nodo nuevo.</p>}

          {tab === 'plan' && (
            <div className="node-form">
              <label className="field">
                <span>Nombre (minúsculas, dígitos, - y _)</span>
                <input value={plan.name} disabled={!isNew} onChange={(e) => change({ ...plan, name: e.target.value })} />
              </label>
              <label className="field">
                <span>Descripción</span>
                <textarea rows={3} value={plan.description ?? ''} onChange={(e) => change({ ...plan, description: e.target.value })} />
              </label>
              <label className="field">
                <span>Idioma de los textos de los agentes</span>
                <select value={String(plan.settings?.language ?? 'es')} onChange={(e) => change({ ...plan, settings: { ...plan.settings, language: e.target.value } })}>
                  <option value="es">es</option>
                  <option value="en">en</option>
                </select>
              </label>
              <JsonField label="inputs (JSON)" value={plan.inputs ?? {}} onValid={(inputs) => change({ ...plan, inputs: inputs as PlanJson['inputs'] })} />
              <JsonField label="settings (JSON)" value={plan.settings ?? {}} onValid={(settings) => change({ ...plan, settings: settings as PlanJson['settings'] })} />
            </div>
          )}

          {tab === 'problemas' && <IssueRows issues={issues} />}

          {tab === 'json' && (
            <div className="node-form">
              <textarea className="mono" rows={26} value={jsonText} onChange={(e) => { setJsonText(e.target.value); setJsonError('') }} />
              {jsonError && <p className="error small">{jsonError}</p>}
              <button
                className="btn small"
                onClick={() => {
                  try {
                    const parsed = JSON.parse(jsonText) as PlanJson
                    if (!parsed || typeof parsed !== 'object' || !parsed.nodes) throw new Error('falta "nodes"')
                    change({ ...parsed, layout: parsed.layout ?? plan.layout })
                    setSelection(null)
                  } catch (error) {
                    setJsonError(`JSON no válido: ${String(error)}`)
                  }
                }}
              >
                Aplicar JSON
              </button>
            </div>
          )}
        </aside>
      </div>

      {connect && (
        <div className="modal-back" onClick={() => setConnect(null)}>
          <div className="modal card" onClick={(e) => e.stopPropagation()}>
            <h3>
              Conectar <code>{connect.from}</code> → <code>{connect.to}</code>
            </h3>
            <p className="dim small">¿Con qué evento de <code>{connect.from}</code> se ejecuta <code>{connect.to}</code>?</p>
            <ConnectChoice
              events={events}
              used={plan.edges.filter((e) => e.to === connect.to && e.from.startsWith(`${connect.from}.`)).map((e) => e.from.split('.')[1])}
              onPick={(event) => {
                change(addEdge(plan, connect.from, event, connect.to))
                setConnect(null)
              }}
              onCancel={() => setConnect(null)}
            />
          </div>
        </div>
      )}
    </div>
  )
}

function ConnectChoice({ events, used, onPick, onCancel }: { events: string[]; used: string[]; onPick: (event: string) => void; onCancel: () => void }) {
  const [custom, setCustom] = useState('')
  return (
    <div>
      <div className="row wrap">
        {events.map((event) => (
          <button key={event} className="btn small" disabled={used.includes(event)} onClick={() => onPick(event)} title={used.includes(event) ? 'Ya conectado' : ''}>
            {event}
          </button>
        ))}
      </div>
      <label className="field">
        <span>Otro evento (p. ej. el de un script o max_attempts)</span>
        <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="nombre_del_evento" />
      </label>
      <div className="row">
        <button className="btn small" disabled={!/^[a-z][a-z0-9_]*$/.test(custom)} onClick={() => onPick(custom)}>
          Usar «{custom || '…'}»
        </button>
        <button className="btn small" onClick={onCancel}>
          Cancelar
        </button>
      </div>
    </div>
  )
}

function JsonField({ label, value, onValid }: { label: string; value: unknown; onValid: (value: unknown) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2))
  const [error, setError] = useState('')
  return (
    <label className="field">
      <span>{label}</span>
      <textarea
        className="mono"
        rows={6}
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          try {
            onValid(JSON.parse(e.target.value))
            setError('')
          } catch (err) {
            setError(String(err))
          }
        }}
      />
      {error && <span className="error small">JSON no válido: {error}</span>}
    </label>
  )
}
