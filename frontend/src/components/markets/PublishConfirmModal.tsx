import ConfirmModal from '../ui/ConfirmModal'

interface PublishConfirmModalProps {
  isSubmitting: boolean
  onCancel: () => void
  onConfirm: () => void
}

/** The question asked before a market is published, on both screens that
 * publish one — the markets list's row and the create form (#512). Written
 * once so the two cannot drift: publication is one way (ADR 0008), and an
 * admin should be told the same thing about it wherever they press Publish. */
export default function PublishConfirmModal({ isSubmitting, onCancel, onConfirm }: PublishConfirmModalProps) {
  return (
    <ConfirmModal
      ariaLabel="Publish market"
      title="Publish this market?"
      description="Traders can see it and trade in it immediately. There is no unpublish, and the terms cannot be edited afterwards."
      isSubmitting={isSubmitting}
      confirmLabel="Confirm Publish"
      confirmingLabel="Publishing…"
      onCancel={onCancel}
      onConfirm={onConfirm}
    />
  )
}
