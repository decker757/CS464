import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import TopBar from './TopBar'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function renderTopBar(user: User | null) {
  return render(
    <WithProviders user={user}>
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('TopBar', () => {
  it('links the logo to the market list for a signed-in user', () => {
    renderTopBar(trader)

    expect(screen.getByRole('link', { name: /predictsmu home/i })).toHaveAttribute('href', '/markets')
  })

  it('links the logo to the landing page for a guest', () => {
    renderTopBar(null)

    expect(screen.getByRole('link', { name: /predictsmu home/i })).toHaveAttribute('href', '/')
  })
})
