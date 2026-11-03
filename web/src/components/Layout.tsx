import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import { useRealtime } from '../realtime'

export function Layout() {
  const connected = useRealtime()
  const stats = useQuery({ queryKey: ['stats'], queryFn: api.stats, refetchInterval: 15000 })
  const pending = stats.data?.pending_approvals ?? 0

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
        </nav>
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
