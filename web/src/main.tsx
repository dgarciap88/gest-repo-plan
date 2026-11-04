import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { RouterProvider, createHashRouter } from 'react-router-dom'
import './styles.css'
import { Layout } from './components/Layout'
import { Approvals } from './pages/Approvals'
import { CatalogPage } from './pages/Catalog'
import { Dashboard } from './pages/Dashboard'
import { PlanEditor } from './pages/PlanEditor'
import { PlanPage } from './pages/PlanPage'
import { Plans } from './pages/Plans'
import { RunPage } from './pages/RunPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: true } },
})

// HashRouter: el backend sirve un único index.html y no necesita reglas de reescritura.
const router = createHashRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <Dashboard /> },
      { path: '/runs/:id', element: <RunPage /> },
      { path: '/approvals', element: <Approvals /> },
      { path: '/plans', element: <Plans /> },
      { path: '/plans/new', element: <PlanEditor /> },
      { path: '/plans/:name/edit', element: <PlanEditor /> },
      { path: '/plans/:name', element: <PlanPage /> },
      { path: '/catalog', element: <CatalogPage /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
)
