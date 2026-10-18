import { useEffect, useState } from 'react'
import { setSuspension, type SuspensionResult } from '../api/authApi'
import { useReasonAction } from './useReasonAction'

// What to show for each documented code (auth-service.md, PATCH
// /admin/users/{user_id}/suspension). Both directions share one set: the
// endpoint reuses the role change's own codes for the two it has in common.
const CODE_MESSAGES: Record<string, string> = {
  cannot_suspend_self: 'You cannot suspend your own account.',
  last_administrator: 'This would leave no administrator who can log in. Promote someone else first.',
}

/** Suspending or unsuspending an account ([4.2] #14): the reason popup's
 * state, the call, and what to show for each documented error. Takes no
 * prior read of the account's current state — `suspended` is the state the
 * caller is asking for, not a toggle, so the same function opens either
 * direction. `onChanged` fires with the server's whole result, including
 * `takes_effect_within_seconds` — never zero (auth-service.md) — since
 * nothing here assumed what the account's state was before the call. */
export function useSuspension(userId: string, onChanged: (result: SuspensionResult) => void) {
  // Which direction the open modal is asking for: true to suspend, false to
  // unsuspend (the same orientation as `suspended` in the request body),
  // null when no modal is open.
  const [direction, setDirection] = useState<boolean | null>(null)

  const { reason, setReason, submitting, formError, setFormError, reasonError, canConfirm, handleConfirm, resetReason } = useReasonAction({
    submit: reason => {
      if (direction === null) throw new Error('no direction chosen')
      return setSuspension(userId, direction, reason)
    },
    incompleteCode: 'suspension_incomplete',
    codeMessages: CODE_MESSAGES,
    genericFailureMessage: 'That failed. Please try again.',
    onDone: result => { setDirection(null); setReason(''); onChanged(result) },
    onError: () => setDirection(null),
  })

  // AdminUserHistoryPage keeps this hook mounted while :userId changes
  // (MarketDetailPage's `loaded` tags its own state the same way) — a
  // message about the last account must not follow the admin to the next one.
  useEffect(() => { setFormError('') }, [userId, setFormError])

  return {
    direction,
    openSuspend: () => setDirection(true),
    openUnsuspend: () => setDirection(false),
    closeModal: () => { setDirection(null); resetReason(); setFormError('') },
    reason,
    setReason,
    submitting,
    formError,
    reasonError,
    canConfirm,
    handleConfirm,
  }
}
