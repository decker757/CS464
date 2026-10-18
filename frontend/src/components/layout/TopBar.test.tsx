import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../../context/AuthContext'
import { WithProviders } from '../../test/renderWithProviders'
import TopBar from './TopBar'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

// `undefined` is its own case, not a repeat of the guest one: the session
// starts unknown and stays that way for a whole /auth/me plus /auth/refresh,
// which is most of a first-time visitor's landing page. A rule written as
// "null means the landing page" would send that visitor's wordmark to a
// protected route and leave the other two cases green.
const cases: Array<[string, User | null | undefined, string]> = [
  ['a signed-in user', trader, '/markets'],
  ['a guest', null, '/'],
  ['a visitor whose session is not known yet', undefined, '/'],
]

function renderTopBar(user: User | null | undefined) {
  render(
    <WithProviders user={user}>
      <MemoryRouter>
        <TopBar />
      </MemoryRouter>
    </WithProviders>,
  )
}

describe('TopBar', () => {
  it.each(cases)('links the logo home for %s', (_who, user, destination) => {
    renderTopBar(user)

    const logoLink = screen.getByRole('link', { name: /predictsmu home/i })
    expect(logoLink).toHaveAttribute('href', destination)
    expect(logoLink).toHaveTextContent('PredictSMU')
  })
})
