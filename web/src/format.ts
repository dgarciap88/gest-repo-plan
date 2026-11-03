// Utilidades de formato (todas en español).

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  return date.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'medium' })
}

export function fmtDuration(start: string | null | undefined, end?: string | null): string {
  if (!start) return '—'
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime()
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`
}

export function fmtCost(usd: number): string {
  return usd ? `${usd.toFixed(4)} USD` : '—'
}

export function fmtInputs(inputs: Record<string, unknown>): string {
  return Object.entries(inputs)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' · ')
}

export function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

/** "hace 3 min", "hace 2 h"… para saber cuándo cambió algo por última vez. */
export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 10) return 'ahora mismo'
  if (s < 60) return `hace ${s} s`
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`
  return `hace ${Math.floor(s / 86400)} d`
}
