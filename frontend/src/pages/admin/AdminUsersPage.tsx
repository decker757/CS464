import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { listUsers, type AdminUser, type AdminUserPage } from '../../api/authApi'
import AppLayout from '../../components/layout/AppLayout'
import Card from '../../components/ui/Card'
import LoadMoreControl from '../../components/ui/LoadMoreControl'
import PageTitle from '../../components/ui/PageTitle'
import SearchForm from '../../components/ui/SearchForm'
import { usePagedList } from '../../hooks/usePagedList'

const pagedUsersOptions = {
  itemsOf: (page: AdminUserPage) => page.users,
  nextCursorOf: (page: AdminUserPage) => page.next_cursor,
  idOf: (user: AdminUser) => user.id,
}

// [FE][4.1] #57. A fragment of a username or an email, matched server-side
// (auth-service.md, GET /admin/users) — blank lists everybody, newest
// registration first, so the screen opens on a list rather than an empty
// state waiting to be typed into.
export default function AdminUsersPage() {
  const [query, setQuery] = useState('')

  const fetchPage = useCallback((cursor: string | undefined) => listUsers({ cursor, q: query || undefined }), [query])
  const { items: users, hasMore, isLoading, hasError, isLoadingMore, loadMoreFailed, loadMore } = usePagedList(fetchPage, pagedUsersOptions)

  return (
    <AppLayout width="max-w-[800px]">
      <PageTitle className="mb-8">Users</PageTitle>

      <div className="mb-6">
        <SearchForm
          id="user-search"
          label="Search by username or email"
          placeholder="Search by username or email"
          query={query}
          onSearch={setQuery}
        />
      </div>

      {isLoading && <p className="text-sm text-muted">Loading users…</p>}

      {hasError && <p role="alert" className="text-sm text-danger">Failed to load users. Please try again.</p>}

      {!isLoading && !hasError && users.length === 0 && (
        <p className="text-sm text-muted">{query ? 'No users match that search.' : 'No users yet.'}</p>
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
