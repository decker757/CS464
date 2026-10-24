import { useCallback, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { listUsers, type AdminUser, type AdminUserPage, type User } from '../../api/authApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import FilterPill from '../../components/ui/FilterPill'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import SearchForm from '../../components/ui/SearchForm'
import { usePagedList } from '../../hooks/usePagedList'

const pagedUsersOptions = {
  itemsOf: (page: AdminUserPage) => page.users,
  nextCursorOf: (page: AdminUserPage) => page.next_cursor,
  idOf: (user: AdminUser) => user.id,
}

interface FilterOption<Value> {
  value: Value | null
  label: string
}

interface FilterGroupProps<Value> {
  label: string
  options: FilterOption<Value>[]
  selected: Value | null
  onSelect: (value: Value | null) => void
}

// null stands for "any", as in MarketFilters, so the pills and what the
// request leaves out are drawn from one place.
const ROLE_OPTIONS: FilterOption<User['role']>[] = [
  { value: null, label: 'Any role' },
  { value: 'admin', label: 'Admins only' },
  { value: 'trader', label: 'Traders only' },
]

// "Suspended only" rather than "Suspended", which is already the word on a
// suspended user's row.
const STATUS_OPTIONS: FilterOption<boolean>[] = [
  { value: null, label: 'Any status' },
  { value: false, label: 'Active only' },
  { value: true, label: 'Suspended only' },
]

// How far each arrow key moves the selection, as in any radio group.
const ARROW_STEPS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

// Names whatever narrowed the list, so an empty one never blames the search
// for what a pill did, or claims there are no users at all.
function emptyMessage(query: string, pillFilterCount: number): string {
  const filters = pillFilterCount === 1 ? 'this filter' : 'these filters'
  if (query && pillFilterCount > 0) return `No users match that search with ${filters}.`
  if (query) return 'No users match that search.'
  if (pillFilterCount > 0) return `No users match ${filters}.`
  return 'No users yet.'
}

// A single-select pill group, so a radio group: one choice is always checked,
// Tab reaches only that one, and the arrow keys move it. Not MarketFilters'
// `aria-pressed` buttons, which announce a multi-select (#442).
function FilterGroup<Value>({ label, options, selected, onSelect }: FilterGroupProps<Value>) {
  const selectedIndex = options.findIndex(option => option.value === selected)

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = ARROW_STEPS[event.key]
    if (step === undefined) return
    event.preventDefault()
    const nextIndex = (selectedIndex + step + options.length) % options.length
    onSelect(options[nextIndex].value)
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]')[nextIndex].focus()
  }

  return (
    <div role="radiogroup" aria-label={label} onKeyDown={handleKeyDown} className="flex flex-wrap gap-2">
      {options.map(option => {
        const isChecked = selected === option.value
        return (
          <FilterPill
            key={option.label}
            role="radio"
            active={isChecked}
            aria-checked={isChecked}
            tabIndex={isChecked ? 0 : -1}
            onClick={() => onSelect(option.value)}
          >
            {option.label}
          </FilterPill>
        )
      })}
    </div>
  )
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
        <FilterGroup label="Filter by role" options={ROLE_OPTIONS} selected={role} onSelect={setRole} />
        <FilterGroup label="Filter by status" options={STATUS_OPTIONS} selected={isSuspended} onSelect={setIsSuspended} />
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
                    <p className="mt-1 text-xs text-subtle">{user.email}</p>
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
