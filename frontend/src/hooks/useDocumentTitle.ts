import { useEffect } from 'react'

// index.html carries the app's own name for the first paint and for anyone
// who lands on a page before React runs. Every page appends to it rather
// than replacing it, so a bookmark still says what the product is.
const APP_NAME = 'PredictSMU'

/**
 * Names the browser tab after the page ([FE] #356).
 *
 * `PageTitle` calls this with its own heading, so the fourteen pages that
 * render one are titled by construction and the tab cannot drift from the
 * `<h1>`. The pages with no `PageTitle` — the three guest pages and the
 * market detail page, which uses the question as its heading — call it
 * directly.
 *
 * A null title is "not known yet", not "no title": the market detail page
 * has no question until its fetch lands, and blanking the tab for that round
 * trip is worse than leaving the previous name up. Restoring on unmount is
 * what stops a page without a title inheriting the last one's.
 */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    if (!title) return
    const previous = document.title
    document.title = `${title} · ${APP_NAME}`
    return () => {
      document.title = previous
    }
  }, [title])
}
