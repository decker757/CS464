import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import ProtectedRoute from './ProtectedRoute'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }
const admin: User = { ...trader, role: 'admin' }

function renderProtectedRoute({
  user,
  requireAdmin = false,
  path = '/',
}: {
  user: User | null | undefined
  requireAdmin?: boolean
  path?: string
}) {
  return render(
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="*"
            element={
              <ProtectedRoute requireAdmin={requireAdmin}>
                <p>protected content</p>
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<p>login page</p>} />
          <Route path="/markets" element={<p>markets page</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('ProtectedRoute', () => {
  it('shows a loading screen while user is loading (undefined)', () => {
    const { container } = renderProtectedRoute({ user: undefined })
    expect(container).not.toBeEmptyDOMElement()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('redirects to /login when user is null', () => {
    renderProtectedRoute({ user: null })
    expect(screen.getByText('login page')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders children when a trader is logged in', () => {
    renderProtectedRoute({ user: trader })
    expect(screen.getByText('protected content')).toBeInTheDocument()
  })

  it('redirects a trader to /markets on an admin-only route', () => {
    renderProtectedRoute({ user: trader, requireAdmin: true })
    expect(screen.getByText('markets page')).toBeInTheDocument()
    expect(screen.queryByText('protected content')).not.toBeInTheDocument()
  })

  it('renders children when an admin visits an admin-only route', () => {
    renderProtectedRoute({ user: admin, requireAdmin: true })
    expect(screen.getByText('protected content')).toBeInTheDocument()
  })
})
