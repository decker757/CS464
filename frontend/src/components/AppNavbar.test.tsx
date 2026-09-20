import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import AppNavbar from './AppNavbar'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function renderNavbar(
  user: User | null | undefined,
  logout = vi.fn().mockResolvedValue(undefined),
) {
  render(
    <AuthContext.Provider value={{ user, login: () => {}, logout }}>
      <MemoryRouter initialEntries={['/markets']}>
        <Routes>
          <Route path="/markets" element={<AppNavbar />} />
          <Route path="/" element={<p>home</p>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
  return { logout }
}

describe('AppNavbar', () => {
  it('shows the brand name', () => {
    renderNavbar(trader)
    expect(screen.getByText('PredictSMU')).toBeInTheDocument()
  })

  it('shows the logged-in username', () => {
    renderNavbar(trader)
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('calls logout and navigates to / when Log Out is clicked', async () => {
    const logout = vi.fn().mockResolvedValue(undefined)
    const actor = userEvent.setup()
    renderNavbar(trader, logout)

    await actor.click(screen.getByRole('button', { name: /log out/i }))

    expect(logout).toHaveBeenCalledOnce()
    expect(screen.getByText('home')).toBeInTheDocument()
  })
})
