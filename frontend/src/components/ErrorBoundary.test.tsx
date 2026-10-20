import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { Link, MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
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

// The create form's shape (#348): it moves its own address from /new to
// /:id/edit once the server has the draft, and must not lose what was typed.
function AddressChangingForm() {
  const navigate = useNavigate()
  const [question, setQuestion] = useState('')
  return (
    <>
      <label htmlFor="question">Question</label>
      <input id="question" value={question} onChange={event => setQuestion(event.target.value)} />
      <button type="button" onClick={() => navigate('/forms/2/edit', { replace: true })}>
        Save
      </button>
    </>
  )
}

function renderAt(path: string) {
  render(
    <WithProviders user={trader}>
      <MemoryRouter initialEntries={[path]}>
        <ErrorBoundary>
          <Routes>
            <Route path="/markets/:id" element={<ThrowingPage />} />
            <Route
              path="/portfolio"
              element={
                <>
                  <p>portfolio page</p>
                  <Link to="/markets/abc">A market that breaks</Link>
                </>
              }
            />
            <Route path="/forms/new" element={<AddressChangingForm />} />
            <Route path="/forms/:id/edit" element={<AddressChangingForm />} />
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

  it('leaves a #fragment out of the reload address', () => {
    renderAt('/markets/abc?tab=history#x')

    // A link to the address already showing plus a fragment is an in-page
    // jump: the browser scrolls and loads nothing, so the button would do
    // nothing at all.
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

  it('catches a page that throws on the very navigation that reached it', async () => {
    // The fault is raised in the same render as the location change. A reset
    // that ran after the fault was recorded, rather than before, would clear
    // it at once and render the throwing page again, outside any boundary.
    const user = userEvent.setup()
    renderAt('/portfolio')

    await user.click(screen.getByRole('link', { name: 'A market that breaks' }))

    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Portfolio' })).toHaveAttribute('href', '/portfolio')
  })

  it('leaves a working page mounted when it changes its own address', async () => {
    // Clearing the fault on navigation is easiest written as a key on the
    // location, and that remounts every page on every navigation: this form
    // would come back empty.
    const user = userEvent.setup()
    renderAt('/forms/new')

    await user.type(screen.getByLabelText('Question'), 'Will it rain?')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByLabelText('Question')).toHaveValue('Will it rain?')
  })
})
