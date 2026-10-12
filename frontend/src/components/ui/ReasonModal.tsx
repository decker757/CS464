import Button from './Button'
import Field from './Field'
import TextArea from './TextArea'

interface ReasonModalProps {
  ariaLabel: string
  title: string
  description: string
  fieldId: string
  reason: string
  onReasonChange: (reason: string) => void
  reasonError?: string
  placeholder: string
  canConfirm: boolean
  isSubmitting: boolean
  confirmLabel: string
  confirmingLabel: string
  onCancel: () => void
  onConfirm: () => void
}

/** The reason-and-confirm popup shared by closing a market early and
 * rejecting a proposed outcome: same overlay, panel, reason field and
 * Cancel/Confirm buttons, differing only in copy and what "confirm" does. */
export default function ReasonModal({
  ariaLabel,
  title,
  description,
  fieldId,
  reason,
  onReasonChange,
  reasonError,
  placeholder,
  canConfirm,
  isSubmitting,
  confirmLabel,
  confirmingLabel,
  onCancel,
  onConfirm,
}: ReasonModalProps) {
  return (
    <div role="dialog" aria-modal="true" aria-label={ariaLabel} className="fixed inset-0 flex items-center justify-center bg-smu-navy/40 px-4">
      <div className="w-full max-w-[480px] rounded-2xl border border-smu-gold/15 bg-white p-6 shadow-card">
        <h2 className="mb-1 text-[17px] font-bold text-smu-navy">{title}</h2>
        <p className="mb-4 text-xs text-subtle">{description}</p>
        <Field id={fieldId} label="Reason *" error={reasonError}>
          <TextArea
            id={fieldId}
            invalid={!!reasonError}
            value={reason}
            onChange={e => onReasonChange(e.target.value)}
            placeholder={placeholder}
          />
        </Field>
        <div className="mt-4 flex justify-end gap-3">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" onClick={onConfirm} disabled={!canConfirm || isSubmitting}>
            {isSubmitting ? confirmingLabel : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}
