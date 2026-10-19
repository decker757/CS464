import Button from './Button'
import ModalShell from './ModalShell'

interface ConfirmModalProps {
  ariaLabel: string
  title: string
  description: string
  isSubmitting: boolean
  confirmLabel: string
  confirmingLabel: string
  onCancel: () => void
  onConfirm: () => void
}

/** A confirm popup with no reason to collect — settling a market takes no
 * body, so there is nothing to type, only a decision to confirm before an
 * irreversible payout. */
export default function ConfirmModal({
  ariaLabel,
  title,
  description,
  isSubmitting,
  confirmLabel,
  confirmingLabel,
  onCancel,
  onConfirm,
}: ConfirmModalProps) {
  return (
    <ModalShell ariaLabel={ariaLabel} title={title} description={description}>
      <div className="mt-4 flex justify-end gap-3">
        <Button variant="outline" size="sm" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" onClick={onConfirm} disabled={isSubmitting}>
          {isSubmitting ? confirmingLabel : confirmLabel}
        </Button>
      </div>
    </ModalShell>
  )
}
