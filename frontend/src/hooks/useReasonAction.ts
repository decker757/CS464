import { useState } from 'react'
import { errorCode, errorDetails } from '../api/errors'

export const MIN_REASON_LENGTH = 10

interface ReasonActionOptions<T> {
  /** Makes the call. Resolves with whatever the caller needs on success. */
  submit: (reason: string) => Promise<T>
  /** The 422 code naming a field error on `reason`, e.g. `close_incomplete`. */
  incompleteCode: string
  /** What to show for each documented non-field error code. */
  codeMessages: Record<string, string>
  /** Codes meaning the action can never succeed without a fresh read of
   * whatever it acted on — the caller shows a reload control, not a retry.
   * Defaults to none. */
  supersedingCodes?: Set<string>
  /** The message shown when the error carries no documented code. */
  genericFailureMessage: string
  /** Fires once the call succeeds, with its result. */
  onDone: (result: T) => void
  /**
   * Fires once the call fails with anything other than the field error on
   * `reason`. Every caller closes its modal here: the error message is
   * painted on the page behind the modal's full-screen overlay, so a modal
   * left open hides it as surely as not showing it at all.
   */
  onError: () => void
}

/** The reason-popup shape shared by every admin action that collects a
 * reason and submits it: close ([2.3] #7), settle, suspend/unsuspend
 * ([4.2] #14), and dispute a result ([3.3] #11). A caller owns its own
 * "which modal, if any, is open" state — `useSuspension`'s is a direction,
 * `useCloseMarket`'s is a boolean — so this only owns the reason field, the
 * submit, and sorting one error into a field message, a looked-up message,
 * the generic fallback, or a superseded flag. */
export function useReasonAction<T>({
  submit, incompleteCode, codeMessages, supersedingCodes, genericFailureMessage, onDone, onError,
}: ReasonActionOptions<T>) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')
  const [reasonError, setReasonError] = useState('')
  const [superseded, setSuperseded] = useState(false)

  const canConfirm = reason.trim().length >= MIN_REASON_LENGTH

  const resetReason = () => {
    setReason('')
    setReasonError('')
  }

  const handleConfirm = async () => {
    if (submitting) return
    setSubmitting(true)
    setFormError('')
    setReasonError('')
    try {
      const result = await submit(reason.trim())
      onDone(result)
    } catch (err) {
      const code = errorCode(err)
      if (code === incompleteCode) {
        setReasonError(errorDetails(err).find(d => d.field === 'reason')?.message ?? 'Fix the reason and try again.')
        return
      }
      setFormError((code && codeMessages[code]) || genericFailureMessage)
      setSuperseded(!!code && !!supersedingCodes?.has(code))
      onError()
    } finally {
      setSubmitting(false)
    }
  }

  return { reason, setReason, submitting, formError, setFormError, reasonError, superseded, canConfirm, handleConfirm, resetReason }
}
