import dagre from '@dagrejs/dagre'
import type { GraphView } from './api'

export type Direction = 'TB' | 'LR'

export const NODE_W = 210
export const NODE_H = 86

export interface GraphLayout {
  positions: Record<string, { x: number; y: number }>
  width: number
  height: number
}

/**
 * Disposición por capas (dagre). Las aristas de retorno (bucles) no cuentan: así el camino principal queda
 * ordenado y sin cruces, y los bucles se dibujan aparte por un lateral. Si el plan trae posiciones propias, se respetan.
 */
export function layoutGraph(graph: GraphView, direction: Direction): GraphLayout {
  if (graph.custom_layout) {
    const xs = Object.values(graph.layout).map((p) => p.x)
    const ys = Object.values(graph.layout).map((p) => p.y)
    return {
      positions: graph.layout,
      width: Math.max(...xs, 0) + NODE_W,
      height: Math.max(...ys, 0) - Math.min(...ys, 0) + NODE_H,
    }
  }
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: direction, nodesep: direction === 'TB' ? 50 : 36, ranksep: 56, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const id of Object.keys(graph.nodes)) g.setNode(id, { width: NODE_W, height: NODE_H })
  const seen = new Set<string>()
  for (const edge of graph.edges) {
    const key = `${edge.from}>${edge.to}`
    if (edge.back || seen.has(key) || edge.from === edge.to) continue
    seen.add(key)
    g.setEdge(edge.from, edge.to)
  }
  dagre.layout(g)
  const positions: GraphLayout['positions'] = {}
  for (const id of Object.keys(graph.nodes)) {
    const node = g.node(id)
    positions[id] = { x: node.x - NODE_W / 2, y: node.y - NODE_H / 2 }
  }
  const info = g.graph()
  return { positions, width: info.width ?? 0, height: info.height ?? 0 }
}
