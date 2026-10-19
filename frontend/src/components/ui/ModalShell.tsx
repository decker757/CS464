import { useEffect, useRef } from 'react'

interface ModalShellProps {
  ariaLabel: string
  title: string
  description: string
  /** What Escape and a click on the backdrop do: the same thing the caller's
   * Cancel button does. */
  onClose: () => void
  /** True while the caller's request is in flight. Escape and the backdrop go
   * inert then, for the same reason every caller disables its Cancel button. */
  isSubmitting?: boolean
  children?: React.ReactNode
}

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

/** The overlay and panel shared by every confirm-style popup — ReasonModal
 * and ConfirmModal differ only in what goes between the description and the
 * buttons, and in what "confirm" does.
 *
 * `aria-modal="true"` promises assistive technology that the background is
 * inert. Keeping that promise is this component's other job, and the reason
 * it is worth having: focus moves in when the dialog opens, Tab cannot leave
 * it, Escape and the backdrop dismiss it, and focus goes back to whatever
 * opened it. Every caller inherits all four (#358). */
export default function ModalShell({ ariaLabel, title, description, onClose, isSubmitting = false, children }: ModalShellProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Whatever was focused when the dialog opened is the control that opened
    // it, and where focus belongs again once it closes.
    const opener = document.activeElement
    const firstFocusable = focusableIn(panelRef.current)[0]
    if (firstFocusable) firstFocusable.focus()
    else panelRef.current?.focus()
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [])

  useEffect(() => {
    // Both keys listen on the document rather than the panel, because focus
    // does not stay in the panel by itself: a Confirm button that disables
    // itself on submit is blurred by the browser, and an ignored backdrop
    // click blurs too, both leaving focus on <body>. A panel-scoped handler
    // never runs again from there, and one Tab would reach the navbar behind
    // the overlay, which is neither disabled nor inert.
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!isSubmitting) onClose()
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
  }, [isSubmitting, onClose])

  // Only the backdrop itself dismisses: a click that started on the panel
  // bubbles up to here as well, and must not close the dialog.
  const handleOverlayClick = (event: React.MouseEvent) => {
    if (event.target === event.currentTarget && !isSubmitting) onClose()
  }

  return (
    <div onClick={handleOverlayClick} className="fixed inset-0 flex items-center justify-center bg-smu-navy/40 px-4">
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        className="w-full max-w-[480px] rounded-2xl border border-smu-gold/15 bg-white p-6 shadow-card"
      >
        <h2 className="mb-1 text-[17px] font-bold text-smu-navy">{title}</h2>
        <p className="mb-4 text-xs text-subtle">{description}</p>
        {children}
      </div>
    </div>
  )
}
