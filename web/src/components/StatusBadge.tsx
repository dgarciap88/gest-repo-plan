const LABELS: Record<string, [string, string]> = {
  succeeded: ['✓', 'Correcto'],
  failed: ['✕', 'Fallido'],
  running: ['◐', 'En ejecución'],
  waiting: ['⏸', 'Esperando'],
  waiting_approval: ['✋', 'Esperando aprobación'],
  waiting_external: ['⏳', 'Esperando'],
  ready: ['▸', 'Listo'],
  pending: ['○', 'Pendiente'],
  queued: ['◌', 'En cola'],
  skipped: ['⤼', 'Saltado'],
  cancelled: ['⊘', 'Cancelado'],
  interrupted: ['⚡', 'Interrumpido'],
}

/** Estado con icono y color (nunca solo color, por accesibilidad). */
export function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  const [icon, label] = LABELS[status] ?? ['•', status]
  return (
    <span className={`badge st-${status}`} title={label}>
      <span className={status === 'running' ? 'spin' : undefined} aria-hidden>
        {icon}
      </span>
      {!compact && <span>{label}</span>}
    </span>
  )
}

export function statusLabel(status: string): string {
  return LABELS[status]?.[1] ?? status
}
