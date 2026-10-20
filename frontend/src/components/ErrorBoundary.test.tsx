import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../context/AuthContext'
import { WithProviders } from '../test/renderWithProviders'
import ErrorBoundary from './ErrorBoundary'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

// What a real render fault says: something internal that no user can act on.
const FAULT_MESSAGE = 'Cannot read properties of undefined (reading price)'

function ThrowingPage(): never {
  throw new Error(FAULT_MESSAGE)
}

function renderAt(path: string) {
  render(
    <WithProviders user={trader}>
      <MemoryRouter initialEntries={[path]}>
        <ErrorBoundary>
          <Routes>
            <Route path="/markets/:id" element={<ThrowingPage />} />
            <Route path="/portfolio" element={<p>portfolio page</p>} />
          </Routes>
        </ErrorBoundary>
      </MemoryRouter>
    </WithProviders>,
  )
}

beforeEach(() => {
  // React reports every error a boundary catches through console.error. That
  // is the diagnostic a developer wants, and only noise in the test output.
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ErrorBoundary', () => {
  it('shows a recovery panel under the navigation when a page throws while rendering', () => {
    renderAt('/markets/abc')

    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument()
    // The navigation is what the issue says must survive: without it the
    // only way off the panel is the address bar.
    expect(screen.getByRole('link', { name: 'Portfolio' })).toHaveAttribute('href', '/portfolio')
  })

  it('says what to do, and nothing about what went wrong inside', () => {
    renderAt('/markets/abc')

    expect(screen.getByRole('alert')).toHaveTextContent(/reload/i)
    // Neither the message nor a stack frame, which would name the component.
    expect(document.body).not.toHaveTextContent(FAULT_MESSAGE)
    expect(document.body).not.toHaveTextContent('ThrowingPage')
  })

  it('reloads the address that failed, query string included', () => {
    renderAt('/markets/abc?tab=history')

    // A link rather than location.reload(): loading the same address again is
    // a reload, and jsdom will not let a test watch location.reload.
    expect(screen.getByRole('link', { name: 'Reload the page' })).toHaveAttribute('href', '/markets/abc?tab=history')
  })

  it('clears the fault once the user navigates somewhere else', async () => {
    const user = userEvent.setup()
    renderAt('/markets/abc')

    await user.click(screen.getByRole('link', { name: 'Portfolio' }))

    // Otherwise the panel outlives the page that broke, and every link in the
    // navigation changes the address under it and nothing else.
    expect(await screen.findByText('portfolio page')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Something went wrong' })).not.toBeInTheDocument()
  })
})
