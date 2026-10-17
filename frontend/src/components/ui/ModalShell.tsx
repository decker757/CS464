interface ModalShellProps {
  ariaLabel: string
  title: string
  description: string
  children?: React.ReactNode
}

/** The overlay and panel shared by every confirm-style popup — ReasonModal
 * and ConfirmModal differ only in what goes between the description and the
 * buttons, and in what "confirm" does. */
export default function ModalShell({ ariaLabel, title, description, children }: ModalShellProps) {
  return (
    <div role="dialog" aria-modal="true" aria-label={ariaLabel} className="fixed inset-0 flex items-center justify-center bg-smu-navy/40 px-4">
      <div className="w-full max-w-[480px] rounded-2xl border border-smu-gold/15 bg-white p-6 shadow-card">
        <h2 className="mb-1 text-[17px] font-bold text-smu-navy">{title}</h2>
        <p className="mb-4 text-xs text-subtle">{description}</p>
        {children}
      </div>
    </div>
  )
}
