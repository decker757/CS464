import { useRef } from 'react'
import { useFocusTrap } from '../../hooks/useFocusTrap'

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

/** The overlay and panel shared by every confirm-style popup — ReasonModal
 * and ConfirmModal differ only in what goes between the description and the
 * buttons, and in what "confirm" does.
 *
 * `aria-modal="true"` promises assistive technology that the background is
 * inert. Keeping that promise is this component's other job, and the reason
 * it is worth having: focus moves in when the dialog opens, Tab cannot leave
 * it, Escape dismisses it, and focus goes back to whatever opened it. Those
 * four are `useFocusTrap`, shared with the navbar's account menu; the
 * backdrop below is this component's own. Every caller inherits all of it
 * (#358). */
export default function ModalShell({ ariaLabel, title, description, onClose, isSubmitting = false, children }: ModalShellProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFocusTrap(panelRef, { onClose, isLocked: isSubmitting })

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
