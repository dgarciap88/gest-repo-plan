import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

interface TransitionEvent {
  run_id: string
  node_id: string | null
}

/**
 * Se suscribe al canal SSE del backend. Cada transición invalida las consultas afectadas
 * (agrupadas en una ventana corta para no refrescar en cascada). Devuelve si hay conexión.
 */
export function useRealtime(): boolean {
  const client = useQueryClient()
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const source = new EventSource('/api/events')
    let timer: number | undefined
    const runs = new Set<string>()
    const nodes = new Set<string>()

    const flush = () => {
      timer = undefined
      client.invalidateQueries({ queryKey: ['runs'] })
      client.invalidateQueries({ queryKey: ['stats'] })
      client.invalidateQueries({ queryKey: ['approvals'] })
      runs.forEach((id) => client.invalidateQueries({ queryKey: ['run', id] }))
      nodes.forEach((key) => client.invalidateQueries({ queryKey: ['node', ...key.split('/')] }))
      runs.clear()
      nodes.clear()
    }

    source.onopen = () => {
      setConnected(true)
      flush() // al (re)conectar se refresca todo, por si se perdieron eventos
    }
    source.onerror = () => setConnected(false)
    source.addEventListener('transition', (message) => {
      const event: TransitionEvent = JSON.parse((message as MessageEvent).data)
      runs.add(event.run_id)
      if (event.node_id) nodes.add(`${event.run_id}/${event.node_id}`)
      if (timer === undefined) timer = window.setTimeout(flush, 150)
    })
    return () => {
      source.close()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [client])

  return connected
}
