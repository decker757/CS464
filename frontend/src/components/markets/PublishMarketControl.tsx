import Button from '../ui/Button'
import type { usePublishMarket } from '../../hooks/usePublishMarket'
import PublishConfirmModal from './PublishConfirmModal'

interface PublishMarketControlProps {
  // The creator's own submitted market: market_service scopes `publish` to the
  // caller, and publication is one way (ADR 0008).
  canPublish: boolean
  publishAction: ReturnType<typeof usePublishMarket>
}

// Publish ([1.3] #3): the row's Publish button and its confirmation.
export default function PublishMarketControl({ canPublish, publishAction }: PublishMarketControlProps) {
  return (
    <>
      {canPublish && !publishAction.superseded && (
        <Button variant="outline" size="xs" onClick={publishAction.openPublishModal}>
          Publish
        </Button>
      )}
      {publishAction.showPublishModal && (
        <PublishConfirmModal isSubmitting={publishAction.publishing} onCancel={publishAction.closePublishModal} onConfirm={publishAction.handlePublish} />
      )}
    </>
  )
}
