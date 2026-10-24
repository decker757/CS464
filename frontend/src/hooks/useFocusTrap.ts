import { useEffect, type RefObject } from 'react'

// Everything inside the panel a keyboard can reach. A disabled Confirm button
// is skipped, so the trap wraps around whatever is actually usable.
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusableIn(panel: HTMLElement | null): HTMLElement[] {
  if (!panel) return []
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

interface FocusTrapOptions {
  /** What Escape does: the same thing the caller's own dismiss control does. */
  onClose: () => void
  /** True while the panel must not be dismissed — a request in flight.
   * Escape goes inert then, for the same reason a caller disables its Cancel
   * button. The trap itself stays on. */
  isLocked?: boolean
}

/**
 * The four promises an open panel over the page has to keep: focus moves in
 * when it opens, Tab cannot leave it, Escape closes it, and focus goes back
 * to whatever opened it.
 *
 * ModalShell needs all four to make `aria-modal="true"` true (#358), and the
 * navbar's account menu needs the same four (#437) — one implementation, so
 * the two cannot drift into subtly different keyboard behaviour.
 *
 * Call it from the component that renders the panel itself, which mounts only
 * while the panel is open: moving focus in and handing it back are this
 * hook's mount and unmount.
 */
export function useFocusTrap(panelRef: RefObject<HTMLElement | null>, { onClose, isLocked = false }: FocusTrapOptions): void {
  useEffect(() => {
    // Whatever was focused when the panel opened is the control that opened
    // it, and where focus belongs again once it closes.
    const opener = document.activeElement
    const firstFocusable = focusableIn(panelRef.current)[0]
    if (firstFocusable) firstFocusable.focus()
    else panelRef.current?.focus()
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [panelRef])

  useEffect(() => {
    // Both keys listen on the document rather than the panel, because focus
    // does not stay in the panel by itself: a Confirm button that disables
    // itself on submit is blurred by the browser, and an ignored backdrop
    // click blurs too, both leaving focus on <body>. A panel-scoped handler
    // never runs again from there, and one Tab would reach the navbar behind
    // the overlay, which is neither disabled nor inert.
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!isLocked) onClose()
        return
      }
      if (event.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      const focusable = focusableIn(panel)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const focused = document.activeElement
      const isInPanel = focused instanceof Node && panel.contains(focused)
      if (!isInPanel) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && focused === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && focused === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [panelRef, isLocked, onClose])
}
