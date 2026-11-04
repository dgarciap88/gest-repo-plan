import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo, useState } from 'react'
import type { GraphView, NodeState } from '../api'
import { type Direction, layoutGraph } from '../layout'
import { StatusBadge } from './StatusBadge'

interface NodeData extends Record<string, unknown> {
  id: string
  type: string
  sideEffect: string
  description: string
  state?: NodeState
  selected: boolean
  direction: Direction
}
type PlanNodeType = Node<NodeData, 'plan'>

function PlanNode({ data }: NodeProps<PlanNodeType>) {
  const status = data.state?.status
  const vertical = data.direction === 'TB'
  // los bucles salen por un lateral (derecha en vertical, abajo en horizontal) para no tapar las aristas de ida
  const loopSide = vertical ? Position.Right : Position.Bottom
  return (
    <div className={`pnode ${status ? `st-${status}` : 'st-plain'} ${data.selected ? 'sel' : ''}`} title={data.description}>
      <Handle type="target" position={vertical ? Position.Top : Position.Left} />
      <div className="pn-head">
        <span className="pn-id">{data.id}</span>
        {data.sideEffect === 'external' && (
          <span className="pn-lock" title="Efecto externo: exige aprobación manual">
            🔒
          </span>
        )}
      </div>
      <div className="pn-type">{data.type}</div>
      {data.state && (
        <div className="pn-foot">
          <StatusBadge status={data.state.status} />
          {data.state.attempts > 1 && <span className="chip">×{data.state.attempts}</span>}
        </div>
      )}
      <Handle type="source" position={vertical ? Position.Bottom : Position.Right} />
      <Handle id="loop-out" type="source" position={loopSide} className="loop-handle" />
      <Handle id="loop-in" type="target" position={loopSide} className="loop-handle" />
    </div>
  )
}

const nodeTypes = { plan: PlanNode }

/** Reencuadra cuando los nodos ya están medidos y cada vez que cambia la disposición. */
function FitOnLayout({ token }: { token: string }) {
  const { fitView } = useReactFlow()
  const ready = useNodesInitialized()
  useEffect(() => {
    if (!ready) return
    const timer = window.setTimeout(() => fitView({ padding: 0.12 }), 60)
    return () => window.clearTimeout(timer)
  }, [ready, token, fitView])
  return null
}

interface Props {
  graph: GraphView
  states?: Record<string, NodeState>
  selected?: string | null
  onSelect?: (id: string) => void
  height?: number | string
}

const FAILURE_EVENT = /failed|rejected|timeout|stalled|expired|max_retries|no_progress|budget|cancelled|some_failed/

function savedDirection(): Direction {
  try {
    return localStorage.getItem('gp_graph_dir') === 'LR' ? 'LR' : 'TB'
  } catch {
    return 'TB'
  }
}

export function Graph({ graph, states, selected, onSelect, height = 560 }: Props) {
  const [direction, setDirection] = useState<Direction>(savedDirection)
  const layout = useMemo(() => layoutGraph(graph, direction), [graph, direction])
  const choose = (value: Direction) => {
    setDirection(value)
    try {
      localStorage.setItem('gp_graph_dir', value)
    } catch {
      /* sin almacenamiento: solo se pierde la preferencia */
    }
  }
  // en vertical el grafo crece hacia abajo: se da altura para leerlo sin encoger demasiado
  const boxHeight = direction === 'TB' && typeof height === 'number' ? Math.min(Math.max(height, layout.height + 40), 900) : height

  const outgoing = useMemo(() => {
    const events: Record<string, Set<string>> = {}
    for (const edge of graph.edges) (events[edge.from] ??= new Set()).add(edge.event)
    return events
  }, [graph])

  const nodes = useMemo<PlanNodeType[]>(
    () =>
      Object.entries(graph.nodes).map(([id, meta]) => ({
        id,
        type: 'plan',
        position: layout.positions[id] ?? { x: 0, y: 0 },
        draggable: false,
        data: {
          id,
          type: meta.type,
          sideEffect: meta.side_effect,
          description: meta.description,
          state: states?.[id],
          selected: selected === id,
          direction,
        },
      })),
    [graph, layout, states, selected, direction],
  )

  const edges = useMemo<Edge[]>(() => {
    // varias aristas entre los mismos nodos (p. ej. tres eventos del validador hacia el mismo destino) se dibujan
    // como una sola, con los eventos juntos en la etiqueta
    const groups = new Map<string, { from: string; to: string; back: boolean; events: string[] }>()
    for (const edge of graph.edges) {
      const key = `${edge.from}>${edge.to}>${edge.back}`
      const group = groups.get(key) ?? { from: edge.from, to: edge.to, back: edge.back, events: [] }
      group.events.push(edge.event)
      groups.set(key, group)
    }
    return [...groups.values()].map((group, index) => {
      const source = states?.[group.from]
      const terminal = source && ['succeeded', 'failed'].includes(source.status)
      const firedEvent = terminal ? group.events.find((event) => event === source.last_event) : undefined
      const fired = firedEvent !== undefined
      const dead = states && terminal && !fired
      const color = fired ? (FAILURE_EVENT.test(firedEvent) ? 'var(--bad)' : 'var(--good)') : 'var(--edge)'
      const many = (outgoing[group.from]?.size ?? 0) > 1
      return {
        id: `${group.from}->${group.to}#${index}`,
        source: group.from,
        target: group.to,
        type: 'smoothstep',
        pathOptions: { borderRadius: 14, offset: group.back ? 30 : 20 },
        sourceHandle: group.back ? 'loop-out' : undefined,
        targetHandle: group.back ? 'loop-in' : undefined,
        // la etiqueta solo aporta cuando el nodo tiene varias salidas (o la arista ya se disparó)
        label: fired ? firedEvent : many ? group.events.join(' · ') : undefined,
        animated: Boolean(fired && states?.[group.to]?.status === 'running'),
        style: {
          stroke: color,
          strokeWidth: fired ? 2.5 : 1.5,
          strokeDasharray: group.back ? '6 4' : undefined,
          opacity: dead ? (group.back ? 0.6 : 0.35) : 1,
        },
        labelStyle: { fill: 'var(--text-dim)', fontSize: 11 },
        labelBgStyle: { fill: 'var(--panel)' },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      }
    })
  }, [graph, states, outgoing])

  return (
    <div className="graph" style={{ height: boxHeight }}>
      <div className="graph-tools">
        <button className={`pill ${direction === 'TB' ? 'active' : ''}`} onClick={() => choose('TB')} title="De arriba abajo">
          ↓ Vertical
        </button>
        <button className={`pill ${direction === 'LR' ? 'active' : ''}`} onClick={() => choose('LR')} title="De izquierda a derecha">
          → Horizontal
        </button>
        <span className="dim small">Rueda: zoom · arrastrar: mover</span>
      </div>
      <ReactFlow
        key={`${direction}-${Object.keys(graph.nodes).join(',')}`}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll
        panOnDrag
        maxZoom={2}
        onNodeClick={(_event, node) => onSelect?.(node.id)}
        proOptions={{ hideAttribution: true }}
        minZoom={0.1}
      >
        <FitOnLayout token={`${direction}-${Math.round(layout.height)}-${boxHeight}`} />
        <Background gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor="var(--edge)" maskColor="rgba(0,0,0,0.18)" style={{ width: 150, height: 96 }} />
      </ReactFlow>
    </div>
  )
}
