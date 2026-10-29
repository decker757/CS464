import Button from '../ui/Button'
import ConfirmModal from '../ui/ConfirmModal'
import type { useSettleMarket } from '../../hooks/useSettleMarket'

interface SettleMarketControlProps {
  // The server's `settleable`, and the row not already settled.
  canSettle: boolean
  settleAction: ReturnType<typeof useSettleMarket>
}

// Settle ([3.4] #12): the row's Settle button and its confirmation.
export default function SettleMarketControl({ canSettle, settleAction }: SettleMarketControlProps) {
  return (
    <>
      {canSettle && !settleAction.superseded && (
        <Button variant="outline" size="xs" onClick={settleAction.openSettleModal}>
          Settle
        </Button>
      )}
      {settleAction.showSettleModal && (
        <ConfirmModal
          ariaLabel="Settle market"
          title="Settle this market?"
          description="Every winning holder is paid out immediately. This cannot be undone."
          isSubmitting={settleAction.settling}
          confirmLabel="Confirm Settle"
          confirmingLabel="Settling…"
          onCancel={settleAction.closeSettleModal}
          onConfirm={settleAction.handleSettle}
        />
      )}
    </>
  )
}
