import { useCallback, useEffect, useState } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import type { AdminUser } from '../../api/authApi'
import { getUserBalance, getUserEntries, type AdminBalance } from '../../api/ledgerApi'
import AppLayout from '../../components/layout/AppLayout'
import LedgerEntryTable from '../../components/ledger/LedgerEntryTable'
import BackLink from '../../components/ui/BackLink'
import Badge from '../../components/ui/Badge'
import Button from '../../components/ui/Button'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import ReasonModal from '../../components/ui/ReasonModal'
import { useAuth } from '../../context/AuthContext'
import { useSuspension } from '../../hooks/useSuspension'
import { usePagedList } from '../../hooks/usePagedList'
import { formatCreditsPrecise } from '../../utils/formatCredits'
import { joinEntryPage, pagedEntryOptions } from '../../utils/ledgerEntries'

// [FE][4.1] #57. Reading another user's balance and history never mints a
// grant, unlike the /me routes a trader's own pages use (ledger-service.md):
// a user who has not read their own yet shows a zero balance and an empty
// history here, not an error.
export default function AdminUserHistoryPage() {
  const { userId } = useParams<{ userId: string }>()
  // Passed from AdminUsersPage's link, so the title can name whose history
  // this is without the ledger knowing usernames. Absent after a page
  // reload, since router state does not survive one — the id is the fallback.
  const location = useLocation()
  const linkedUser = (location.state as { user?: AdminUser } | null)?.user
  // Tagged with the user id it was changed for, so navigating to another
  // user's page shows that one rather than carrying this one's state along
  // (MarketDetailPage's `loaded` does the same for its own id). Replaces
  // linkedUser once a suspend/unsuspend call answers, so the badge and the
  // control repaint from what the server actually did, not from router
  // state that may already be stale by then (useSuspension.ts).
  const [changed, setChanged] = useState<{ userId: string; user: AdminUser; takesEffectWithinSeconds: number } | null>(null)
  const changedUser = changed !== null && changed.userId === userId ? changed.user : null
  const currentUser = changedUser ?? linkedUser ?? null
  const takesEffectNote = changed !== null && changed.userId === userId
    ? `Anything already signed in may keep working for up to ${Math.ceil(changed.takesEffectWithinSeconds / 60)} minutes.`
    : null

  const { user: signedInUser } = useAuth()
  const canActOnThisAccount = userId !== undefined && userId !== signedInUser?.id

  const {
    direction, openSuspend, openUnsuspend, closeModal,
    reason, setReason, submitting, formError, reasonError, canConfirm, handleConfirm,
  } = useSuspension(userId ?? '', result =>
    setChanged({ userId: userId ?? '', user: result.user, takesEffectWithinSeconds: result.takes_effect_within_seconds }))

  const [balance, setBalance] = useState<AdminBalance | null>(null)
  const [balanceError, setBalanceError] = useState(false)

  useEffect(() => {
    if (!userId) return
    let ignored = false
    getUserBalance(userId)
      .then(found => { if (!ignored) setBalance(found) })
      .catch(() => { if (!ignored) setBalanceError(true) })
    // Runs before the next userId's effect, so the previous user's balance
    // never lingers on screen while the next one is still loading, and a
    // late answer for the user just left (ignored) can't land on it either —
    // usePagedList's own listVersion guards the table below the same way.
    return () => {
      ignored = true
      setBalance(null)
      setBalanceError(false)
    }
  }, [userId])

  const fetchPage = useCallback(
    (cursor: string | undefined) => joinEntryPage(() => getUserEntries(userId ?? '', { cursor })),
    [userId],
  )
  const { items: rows, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchPage, pagedEntryOptions)

  return (
    <AppLayout width="max-w-[1100px]">
      <BackLink to="/admin/users" label="Users" className="mb-4 block" />
      <div className="mb-2 flex items-center gap-3">
        <PageTitle>{currentUser ? `${currentUser.username}'s History` : `History for ${userId}`}</PageTitle>
        {currentUser?.is_suspended && <Badge tone="danger">Suspended</Badge>}
      </div>
      {currentUser && <p className="mb-2 text-sm text-subtle">{currentUser.email}</p>}

      {/* Both always shown, not one state-aware toggle: there is no
          GET /admin/users/{id} to confirm current state after a reload, so a
          toggle here could show the wrong label. The Suspended badge above
          is the only state indicator; these two just pick a direction.
          Neither is offered on the signed-in admin's own page
          (auth-service.md: "do not offer the control on the signed-in
          user's own row"); the backend's own refusal is the backstop. */}
      {canActOnThisAccount && (
        <div className="mb-6 flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={openSuspend}>
            Suspend
          </Button>
          <Button variant="outline" size="sm" onClick={openUnsuspend}>
            Unsuspend
          </Button>
        </div>
      )}

      {takesEffectNote && <p className="mb-6 text-sm text-success">Done. {takesEffectNote}</p>}

      {formError && <p role="alert" className="mb-6 text-sm text-danger">{formError}</p>}

      {balanceError && <p role="alert" className="mb-6 text-sm text-danger">Failed to load balance.</p>}
      {balance && (
        <p className="mb-8 text-sm text-muted">
          Current balance: <span className="font-semibold text-smu-navy">{formatCreditsPrecise(balance.balance)} credits</span>
        </p>
      )}

      {isLoading && <p className="text-sm text-muted">Loading history…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load history. Please try again.</p>}

      {!isLoading && !hasError && rows.length === 0 && (
        <p className="text-sm text-muted">No activity for this user yet.</p>
      )}

      {!isLoading && !hasError && rows.length > 0 && (
        <>
          <LedgerEntryTable rows={rows} />

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="history" onLoadMore={loadMore} />
          )}
        </>
      )}

      {direction !== null && (
        <ReasonModal
          ariaLabel={direction ? 'Suspend account' : 'Unsuspend account'}
          title={direction ? 'Suspend this account?' : 'Unsuspend this account?'}
          description={
            direction
              ? 'The account cannot log in or trade. Existing positions are kept and settle as usual. The reason goes into the admin log and cannot be edited afterwards.'
              : 'The account can log in and trade again. The reason goes into the admin log and cannot be edited afterwards.'
          }
          fieldId="suspension-reason"
          reason={reason}
          onReasonChange={setReason}
          reasonError={reasonError}
          placeholder={direction ? 'Explain why this account is being suspended.' : 'Explain why this account is being unsuspended.'}
          canConfirm={canConfirm}
          isSubmitting={submitting}
          confirmLabel={direction ? 'Confirm Suspend' : 'Confirm Unsuspend'}
          confirmingLabel={direction ? 'Suspending…' : 'Unsuspending…'}
          onCancel={closeModal}
          onConfirm={handleConfirm}
        />
      )}
    </AppLayout>
  )
}
