import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import GuestOnly from './GuestOnly'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function renderGuestOnly(user: User | null | undefined) {
  return render(
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<GuestOnly><p>guest content</p></GuestOnly>} />
          <Route path="/markets" element={<p>markets page</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('GuestOnly', () => {
  it('shows a loading screen while user is loading (undefined)', () => {
    const { container } = renderGuestOnly(undefined)
    expect(container).not.toBeEmptyDOMElement()
    expect(screen.queryByText('guest content')).not.toBeInTheDocument()
  })

  it('renders children when nobody is signed in', () => {
    renderGuestOnly(null)
    expect(screen.getByText('guest content')).toBeInTheDocument()
  })

  it('redirects a signed-in user to /markets', () => {
    renderGuestOnly(trader)
    expect(screen.getByText('markets page')).toBeInTheDocument()
    expect(screen.queryByText('guest content')).not.toBeInTheDocument()
  })
})
