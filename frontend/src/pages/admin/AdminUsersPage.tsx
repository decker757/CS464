import { useCallback, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { listUsers, type AdminUserPage } from '../../api/authApi'
import AppLayout from '../../components/layout/AppLayout'
import Button from '../../components/ui/Button'
import Card from '../../components/ui/Card'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import TextInput from '../../components/ui/TextInput'
import { usePagedList } from '../../hooks/usePagedList'

const pagedUsersOptions = {
  itemsOf: (page: AdminUserPage) => page.users,
  nextCursorOf: (page: AdminUserPage) => page.next_cursor,
  idOf: (user: { id: string }) => user.id,
}

// [FE][4.1] #57. A fragment of a username or an email, matched server-side
// (auth-service.md, GET /admin/users) — blank lists everybody, newest
// registration first, so the screen opens on a list rather than an empty
// state waiting to be typed into.
export default function AdminUsersPage() {
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState('')

  const fetchPage = useCallback((cursor: string | undefined) => listUsers({ cursor, q: query || undefined }), [query])
  const { items: users, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchPage, pagedUsersOptions)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setQuery(draft.trim())
  }

  return (
    <AppLayout width="max-w-[800px]">
      <PageTitle className="mb-8">Users</PageTitle>

      <form role="search" onSubmit={handleSubmit} className="mb-6 flex gap-2">
        <label htmlFor="user-search" className="sr-only">Search by username or email</label>
        <TextInput
          id="user-search"
          type="search"
          value={draft}
          onChange={event => setDraft(event.target.value)}
          placeholder="Search by username or email"
        />
        <Button type="submit" variant="outline" size="md">Search</Button>
      </form>

      {isLoading && <p className="text-sm text-muted">Loading users…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load users. Please try again.</p>}

      {!isLoading && !hasError && users.length === 0 && (
        <p className="text-sm text-muted">{query ? 'No users match that search.' : 'No users yet.'}</p>
      )}

      {!isLoading && !hasError && users.length > 0 && (
        <>
          <div className="flex flex-col gap-3">
            {users.map(user => (
              <Link key={user.id} to={`/admin/users/${user.id}`}>
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
