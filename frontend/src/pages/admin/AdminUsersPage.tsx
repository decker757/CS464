import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { listUsers, type AdminUser, type AdminUserPage, type User } from '../../api/authApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import RadioPillGroup, { type RadioPillOption } from '../../components/ui/RadioPillGroup'
import SearchForm from '../../components/ui/SearchForm'
import { usePagedList } from '../../hooks/usePagedList'

const pagedUsersOptions = {
  itemsOf: (page: AdminUserPage) => page.users,
  nextCursorOf: (page: AdminUserPage) => page.next_cursor,
  idOf: (user: AdminUser) => user.id,
}

// null stands for "any", as in MarketFilters, so the pills and what the
// request leaves out are drawn from one place.
const ROLE_OPTIONS: RadioPillOption<User['role'] | null>[] = [
  { value: null, label: 'Any role' },
  { value: 'admin', label: 'Admins only' },
  { value: 'trader', label: 'Traders only' },
]

// "Suspended only" rather than "Suspended", which is already the word on a
// suspended user's row.
const STATUS_OPTIONS: RadioPillOption<boolean | null>[] = [
  { value: null, label: 'Any status' },
  { value: false, label: 'Active only' },
  { value: true, label: 'Suspended only' },
]

// Names whatever narrowed the list, so an empty one never blames the search
// for what a pill did, or claims there are no users at all.
function emptyMessage(query: string, pillFilterCount: number): string {
  const filters = pillFilterCount === 1 ? 'this filter' : 'these filters'
  if (query && pillFilterCount > 0) return `No users match that search with ${filters}.`
  if (query) return 'No users match that search.'
  if (pillFilterCount > 0) return `No users match ${filters}.`
  return 'No users yet.'
}

// [FE][4.1] #57. A fragment of a username or an email, matched server-side
// (auth-service.md, GET /admin/users) — blank lists everybody, newest
// registration first, so the screen opens on a list rather than an empty
// state waiting to be typed into. The role and status filters (#479) narrow
// it on the server too, and combine with the search.
export default function AdminUsersPage() {
  const [query, setQuery] = useState('')
  const [role, setRole] = useState<User['role'] | null>(null)
  const [isSuspended, setIsSuspended] = useState<boolean | null>(null)

  // A new search or filter is a new `fetchPage`, which usePagedList starts
  // again from page one: a cursor continues only the list it came from.
  const fetchPage = useCallback(
    (cursor: string | undefined) =>
      listUsers({ cursor, q: query || undefined, role: role ?? undefined, is_suspended: isSuspended ?? undefined }),
    [query, role, isSuspended],
  )
  const { items: users, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchPage, pagedUsersOptions)
  const pillFilterCount = [role, isSuspended].filter(value => value !== null).length

  return (
    <AppLayout width="max-w-[800px]">
      <PageTitle className="mb-8">Users</PageTitle>

      <div className="mb-6 space-y-4">
        <SearchForm
          id="user-search"
          label="Search by username or email"
          placeholder="Search by username or email"
          query={query}
          onSearch={setQuery}
        />
        <RadioPillGroup label="Filter by role" options={ROLE_OPTIONS} selected={role} onSelect={setRole} />
        <RadioPillGroup label="Filter by status" options={STATUS_OPTIONS} selected={isSuspended} onSelect={setIsSuspended} />
      </div>

      {isLoading && <p className="text-sm text-muted">Loading users…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load users. Please try again.</p>}

      {!isLoading && !hasError && users.length === 0 && (
        <p className="text-sm text-muted">{emptyMessage(query, pillFilterCount)}</p>
      )}

      {!isLoading && !hasError && users.length > 0 && (
        <>
          <div className="flex flex-col gap-3">
            {users.map(user => (
              <Link key={user.id} to={`/admin/users/${user.id}`} state={{ user }}>
                <Card interactive className="flex items-center justify-between gap-4 px-6 py-4">
                  <div className="min-w-0">
                    <p className="truncate text-[15px] font-semibold text-smu-navy">{user.username}</p>
                    {/* break-words: an email can be one unbroken word, wider than a phone (#516). */}
                    <p className="mt-1 text-xs break-words text-subtle">{user.email}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-xs">
                    {user.is_suspended && <span className="font-semibold text-danger">Suspended</span>}
                    <span className="uppercase tracking-[0.5px] text-subtle">{user.role}</span>
                  </div>
                </Card>
              </Link>
            ))}
          </div>

          {hasMore && (
            <LoadMoreControl isLoadingMore={isLoadingMore} hasFailed={loadMoreFailed} noun="users" onLoadMore={loadMore} />
          )}
        </>
      )}
    </AppLayout>
  )
}
