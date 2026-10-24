import { useEffect, useLayoutEffect } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

// How far down each history entry was left, by the key React Router stamps on
// it. A module-level Map rather than sessionStorage: it only has to outlive a
// navigation inside one page load, and a reload starts a document with no list
// in it to scroll anyway.
const offsetByKey = new Map<string, number>()

// How long a restore keeps asking for its offset back. The list is fetched
// after this runs, so for the first frames the page is a heading and "Loading
// markets…" — and 1,400px into that is the bottom of nothing, which is why
// the browser's own restoration lands at 0 ([X-1] #431). Long enough for a
// slow list to arrive, short enough to stop pulling at a page that is
// genuinely shorter than it was.
const RESTORE_WINDOW_MS = 3_000

/**
 * Where a page starts: back where the trader left it on a Back or a Forward,
 * and at the top on every other navigation ([X-1] #431).
 *
 * React Router restores no scroll position by default, and its own
 * `ScrollRestoration` needs a data router, which `App` is not. This is the
 * same idea for a `BrowserRouter`, plus the one thing a data router gets for
 * free: its loaders have the data before the route renders, while every page
 * here fetches after it renders, so the page is still too short to scroll at
 * the moment the Back lands.
 *
 * Renders nothing, and must sit inside the router.
 */
export default function ScrollRestoration() {
  const { key } = useLocation()
  const navigationType = useNavigationType()

  // Recorded as the page scrolls rather than when it is left: by the time the
  // next location has rendered the browser may already have clamped `scrollY`
  // to a shorter page, and that clamped number is not where the trader was.
  useEffect(() => {
    const remember = () => offsetByKey.set(key, window.scrollY)
    window.addEventListener('scroll', remember, { passive: true })
    return () => window.removeEventListener('scroll', remember)
  }, [key])

  useLayoutEffect(() => {
    // A Back or a Forward returns to an entry this has already seen; anything
    // else is a screen the trader has not been on, and starts at the top the
    // way a full page load does.
    const offset = navigationType === 'POP' ? (offsetByKey.get(key) ?? 0) : 0
    if (window.scrollY === offset) return

    let frame = 0
    const giveUpAt = performance.now() + RESTORE_WINDOW_MS
    const restore = () => {
      window.scrollTo(0, offset)
      // `scrollTo` clamps to what the page can scroll, so on a page that has
      // not filled in yet `scrollY` lands short of the offset: ask again as
      // the cards arrive. A pixel of slack, because a fractional device pixel
      // ratio can leave the answer just under the asking price.
      if (window.scrollY >= offset - 1 || performance.now() >= giveUpAt) return
      frame = requestAnimationFrame(restore)
    }
    restore()
    return () => cancelAnimationFrame(frame)
  }, [key, navigationType])

  return null
}
