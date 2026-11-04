import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { useRealtime } from '../realtime'

export function Layout() {
  const connected = useRealtime()
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 15000 })
  const pending = stats.data?.pending_approvals ?? 0
  const notifications = typeof Notification !== 'undefined' ? Notification : undefined
  const [permission, setPermission] = useState(notifications?.permission ?? 'denied')
  const previous = useRef<number | null>(null)

  // título con el nº de aprobaciones y aviso del sistema cuando llega una nueva
  useEffect(() => {
    document.title = pending > 0 ? `(${pending}) gestplan` : 'gestplan'
    if (previous.current !== null && pending > previous.current && permission === 'granted' && notifications) {
      new notifications('gestplan: aprobación pendiente', { body: `${pending} aprobación(es) esperan tu decisión` })
    }
    previous.current = pending
  }, [pending, permission, notifications])

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">gestplan</div>
        <nav>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/approvals">
            Aprobaciones{pending > 0 && <span className="count">{pending}</span>}
          </NavLink>
          <NavLink to="/plans">Planes</NavLink>
          <NavLink to="/catalog">Catálogo</NavLink>
          <NavLink to="/config">Configuración</NavLink>
        </nav>
        {notifications && permission === 'default' && (
          <button className="btn small" onClick={() => notifications.requestPermission().then(setPermission)}>
            Activar avisos
          </button>
        )}
        <div className={`conn ${connected ? 'on' : 'off'}`} title={connected ? 'Tiempo real activo' : 'Sin conexión con el servidor'}>
          <span className="dot" /> {connected ? 'En vivo' : 'Sin conexión'}
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
