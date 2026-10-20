import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App, { AppFrame } from './App'
import { mockUser } from './test/handlers'
import { server } from './test/server'

// The real App, with its real router, providers and routes: what decides that
// an unknown address reaches the 404 page, and that the boundary can still
// draw the navigation, is how App.tsx puts those together.

function openApp(path: string) {
  window.history.pushState({}, '', path)
  render(<App />)
}

function signIn() {
  server.use(
    http.get('http://localhost:8000/auth/me', () => HttpResponse.json(mockUser)),
    http.get('http://localhost:8003/ledger/balances/me', () =>
      HttpResponse.json({ user_id: mockUser.id, account_id: 'acc-1', balance: '1000.0000' }),
    ),
  )
}

afterEach(() => {
  window.history.pushState({}, '', '/')
  vi.restoreAllMocks()
})

describe('an address no route declares', () => {
  it('shows a guest the 404 page inside the app frame, with a way back to the landing page', async () => {
    openApp('/no-such-page')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the home page' })).toHaveAttribute('href', '/')
    // The frame: the wordmark, which is all the navigation a guest has.
    expect(screen.getByRole('link', { name: /predictsmu home/i })).toBeInTheDocument()
  })

  it('shows a signed-in user the navigation and a way back to the markets', async () => {
    signIn()
    openApp('/portfolios')

    expect(await screen.findByRole('link', { name: 'Portfolio' })).toHaveAttribute('href', '/portfolio')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    // The same words as a guest's, and the same home as the wordmark's.
    expect(screen.getByRole('link', { name: 'Go to the home page' })).toHaveAttribute('href', '/markets')
  })
})

describe('a page that throws while rendering', () => {
  it('gets the recovery panel inside the app frame, because the boundary sits inside the providers', () => {
    // #367's review: TopBar reads the session, and AppNavbar the balance too,
    // so a boundary outside either provider throws again while drawing its
    // own panel, and the page goes white anyway.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    function ThrowingPage(): never {
      throw new Error('a render fault')
    }

    render(
      <MemoryRouter initialEntries={['/broken']}>
        <AppFrame>
          <Routes>
            <Route path="/broken" element={<ThrowingPage />} />
          </Routes>
        </AppFrame>
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /predictsmu home/i })).toBeInTheDocument()
  })
})
