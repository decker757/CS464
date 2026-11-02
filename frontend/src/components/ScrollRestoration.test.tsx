import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useLayoutEffect } from 'react'
import { Link, MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ScrollRestoration from './ScrollRestoration'

// jsdom has no layout, so it has no scroll position either: `scrollY` is
// always 0 and `scrollTo` does nothing. These stand in for the browser's, and
// hold the offset still until something scrolls.
let scrollY = 0

function scrollTheWindowTo(offset: number) {
  scrollY = offset
}

/** Where the browser delivers a scroll event: in the frame after the commit
 * that caused it, which can arrive before React has run the previous page's
 * effect cleanups, because React Router renders a navigation as a transition
 * and React leaves that commit's passive effects for a later task. A layout
 * effect is the same moment in a test: after the commit, before the cleanups. */
function LateScrollEvent() {
  useLayoutEffect(() => {
    window.dispatchEvent(new Event('scroll'))
  }, [])
  return null
}

function BackButton() {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>Back</button>
}

beforeEach(() => {
  scrollY = 0
  vi.spyOn(window, 'scrollY', 'get').mockImplementation(() => scrollY)
  vi.spyOn(window, 'scrollTo').mockImplementation(((_x: number, y: number) => scrollTheWindowTo(y)) as typeof window.scrollTo)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ScrollRestoration', () => {
  // #502: the browse list was left at 1,034px, the market page scrolled to
  // the top, and that scroll's event was recorded as the list's offset —
  // so Back restored the list to 0 rather than to where the trader was.
  it('returns a page to its own offset when a scroll on the next page lands before the switch is cleaned up', async () => {
    const actor = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/markets']}>
        <ScrollRestoration />
        <Routes>
          <Route path="/markets" element={<Link to="/markets/1">Open market</Link>} />
          <Route
            path="/markets/:id"
            element={
              <>
                <LateScrollEvent />
                <BackButton />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    )

    scrollTheWindowTo(1034)
    window.dispatchEvent(new Event('scroll'))

    await actor.click(screen.getByRole('link', { name: 'Open market' }))
    // The market page starts at the top, as a screen not seen before should.
    expect(window.scrollY).toBe(0)

    await actor.click(screen.getByRole('button', { name: 'Back' }))

    await screen.findByRole('link', { name: 'Open market' })
    expect(window.scrollY).toBe(1034)
  })
})
