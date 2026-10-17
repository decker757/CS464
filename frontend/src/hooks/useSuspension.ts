import { useState } from 'react'
import { setSuspension, type AdminUser } from '../api/authApi'
import { errorCode, errorDetails } from '../api/errors'

const MIN_REASON_LENGTH = 10

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
 * direction. `onChanged` fires with the server's own updated user, which
 * becomes the page's source of truth, since nothing here assumed what the
 * account's state was before the call. */
export function useSuspension(userId: string, onChanged: (user: AdminUser) => void) {
  // Which direction the open modal is asking for; null when it is closed.
  const [direction, setDirection] = useState<boolean | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [reasonError, setReasonError] = useState('')

  const canConfirm = reason.trim().length >= MIN_REASON_LENGTH

  const handleConfirm = async () => {
    if (submitting || direction === null) return
    setSubmitting(true)
    setFormError('')
    setReasonError('')
    try {
      const result = await setSuspension(userId, direction, reason.trim())
      setDirection(null)
      setReason('')
      onChanged(result.user)
    } catch (err) {
      const code = errorCode(err)
      if (code === 'suspension_incomplete') {
        setReasonError(errorDetails(err).find(d => d.field === 'reason')?.message ?? 'Fix the reason and try again.')
        return
      }
      setFormError((code && CODE_MESSAGES[code]) || 'That failed. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return {
    direction,
    openSuspend: () => setDirection(true),
    openUnsuspend: () => setDirection(false),
    closeModal: () => { setDirection(null); setReason(''); setReasonError('') },
    reason,
    setReason,
    submitting,
    formError,
    reasonError,
    canConfirm,
    handleConfirm,
  }
}
