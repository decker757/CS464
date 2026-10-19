import Button from './Button'
import Field from './Field'
import ModalShell from './ModalShell'
import TextArea from './TextArea'

interface ReasonModalProps {
  ariaLabel: string
  title: string
  description: string
  /** Extra content between the description and the reason field — what is
   * being acted on, for an action (like a dispute) where that is not
   * otherwise on screen. */
  details?: React.ReactNode
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
 * rejecting a proposed outcome: same reason field and Cancel/Confirm
 * buttons, differing only in copy and what "confirm" does. */
export default function ReasonModal({
  ariaLabel,
  title,
  description,
  details,
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
    <ModalShell ariaLabel={ariaLabel} title={title} description={description}>
      {details}
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
    </ModalShell>
  )
}
