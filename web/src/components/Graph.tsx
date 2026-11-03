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
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo } from 'react'
import type { GraphView, NodeState } from '../api'
import { StatusBadge } from './StatusBadge'

interface NodeData extends Record<string, unknown> {
  id: string
  type: string
  sideEffect: string
  description: string
  state?: NodeState
  selected: boolean
}
type PlanNodeType = Node<NodeData, 'plan'>

function PlanNode({ data }: NodeProps<PlanNodeType>) {
  const status = data.state?.status
  return (
    <div className={`pnode ${status ? `st-${status}` : 'st-plain'} ${data.selected ? 'sel' : ''}`} title={data.description}>
      <Handle type="target" position={Position.Left} />
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
      <Handle type="source" position={Position.Right} />
      {/* Los bucles (aristas de retorno) entran y salen por debajo para no tapar las aristas de ida */}
      <Handle id="loop-out" type="source" position={Position.Bottom} className="loop-handle" />
      <Handle id="loop-in" type="target" position={Position.Bottom} className="loop-handle" />
    </div>
  )
}

const nodeTypes = { plan: PlanNode }

interface Props {
  graph: GraphView
  states?: Record<string, NodeState>
  selected?: string | null
  onSelect?: (id: string) => void
  height?: number | string
}

const FAILURE_EVENT = /failed|rejected|timeout|stalled|expired|max_retries|no_progress|budget|cancelled|some_failed/

export function Graph({ graph, states, selected, onSelect, height = 520 }: Props) {
  const nodes = useMemo<PlanNodeType[]>(
    () =>
      Object.entries(graph.nodes).map(([id, meta]) => ({
        id,
        type: 'plan',
        position: graph.layout[id] ?? { x: 0, y: 0 },
        draggable: false,
        data: {
          id,
          type: meta.type,
          sideEffect: meta.side_effect,
          description: meta.description,
          state: states?.[id],
          selected: selected === id,
        },
      })),
    [graph, states, selected],
  )

  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge, index) => {
        const source = states?.[edge.from]
        const terminal = source && ['succeeded', 'failed'].includes(source.status)
        const fired = terminal && source.last_event === edge.event
        const dead = states && terminal && !fired
        const color = fired ? (FAILURE_EVENT.test(edge.event) ? 'var(--bad)' : 'var(--good)') : 'var(--edge)'
        return {
          id: `${edge.from}.${edge.event}->${edge.to}#${index}`,
          source: edge.from,
          target: edge.to,
          type: edge.back ? 'smoothstep' : 'default',
          sourceHandle: edge.back ? 'loop-out' : undefined,
          targetHandle: edge.back ? 'loop-in' : undefined,
          label: edge.event,
          animated: Boolean(fired && states?.[edge.to]?.status === 'running'),
          style: {
            stroke: color,
            strokeWidth: fired ? 2.5 : 1.5,
            strokeDasharray: edge.back ? '6 4' : undefined,
            opacity: dead ? (edge.back ? 0.6 : 0.35) : 1,
          },
          labelStyle: { fill: 'var(--text-dim)', fontSize: 11 },
          labelBgStyle: { fill: 'var(--panel)' },
          markerEnd: { type: MarkerType.ArrowClosed, color },
        }
      }),
    [graph, states],
  )

  return (
    <div className="graph" style={{ height }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        preventScrolling={false}
        onNodeClick={(_event, node) => onSelect?.(node.id)}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
      >
        <Background gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor="var(--edge)" maskColor="rgba(0,0,0,0.18)" style={{ width: 150, height: 96 }} />
      </ReactFlow>
    </div>
  )
}
