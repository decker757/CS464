import { Suspense, lazy } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ErrorBoundary from './components/ErrorBoundary'
import GuestOnly from './components/GuestOnly'
import ProtectedRoute from './components/ProtectedRoute'
import ScrollRestoration from './components/ScrollRestoration'
import { AuthProvider } from './context/AuthContext'
import { BalanceProvider } from './context/BalanceContext'
import LandingPage from './pages/LandingPage'
import LeaderboardPage from './pages/LeaderboardPage'
import LoginPage from './pages/LoginPage'
import MarketDetailPage from './pages/MarketDetailPage'
import MarketsPage from './pages/MarketsPage'
import NotFoundPage from './pages/NotFoundPage'
import PortfolioPage from './pages/PortfolioPage'
import RegisterPage from './pages/RegisterPage'
import TradeHistoryPage from './pages/TradeHistoryPage'
import AdminMarketsPage from './pages/admin/AdminMarketsPage'
import AdminUserHistoryPage from './pages/admin/AdminUserHistoryPage'
import AdminUsersPage from './pages/admin/AdminUsersPage'
import CreateMarketPage from './pages/admin/CreateMarketPage'
import DecideOutcomePage from './pages/admin/DecideOutcomePage'
import MarketExposurePage from './pages/admin/MarketExposurePage'
import ProposalsPage from './pages/admin/ProposalsPage'
import ProposeOutcomePage from './pages/admin/ProposeOutcomePage'

// recharts roughly doubles the main bundle, so it is its own chunk,
// downloaded on visiting this route rather than on every page load. The
// market page's chart is lazy the same way (PriceHistorySection, #453), and
// src/main.test.ts fails if a static import ever pulls recharts into the entry.
const MarketPriceHistoryPage = lazy(() => import('./pages/admin/MarketPriceHistoryPage'))

/**
 * Everything around the routes, inside the router: the session, the balance,
 * and the error boundary, in that order ([FE] #355).
 *
 * The boundary goes innermost because its recovery panel draws the navbar,
 * and the navbar reads both providers: placed outside either one, the panel
 * throws while drawing itself and the page goes white anyway (#367's review).
 */
export function AppFrame({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <BalanceProvider>
        <ErrorBoundary>{children}</ErrorBoundary>
      </BalanceProvider>
    </AuthProvider>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      {/* Inside the router and outside the routes: it reads the history entry
          the router is on, and must outlive the page it is putting back where
          the trader left it ([X-1] #431). */}
      <ScrollRestoration />
      <AppFrame>
        <Routes>
          <Route path="/" element={<GuestOnly><LandingPage /></GuestOnly>} />
          <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
          <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
          <Route path="/markets" element={<ProtectedRoute><MarketsPage /></ProtectedRoute>} />
          <Route path="/markets/:id" element={<ProtectedRoute><MarketDetailPage /></ProtectedRoute>} />
          <Route path="/portfolio" element={<ProtectedRoute><PortfolioPage /></ProtectedRoute>} />
          <Route path="/history" element={<ProtectedRoute><TradeHistoryPage /></ProtectedRoute>} />
          <Route path="/leaderboard" element={<ProtectedRoute><LeaderboardPage /></ProtectedRoute>} />
          <Route path="/admin/markets" element={<ProtectedRoute requireAdmin><AdminMarketsPage /></ProtectedRoute>} />
          <Route path="/admin/users" element={<ProtectedRoute requireAdmin><AdminUsersPage /></ProtectedRoute>} />
          <Route path="/admin/users/:userId" element={<ProtectedRoute requireAdmin><AdminUserHistoryPage /></ProtectedRoute>} />
          <Route path="/admin/markets/new" element={<ProtectedRoute requireAdmin><CreateMarketPage /></ProtectedRoute>} />
          {/* The same form, on a market the server already holds: a draft to
              carry on with, or a submitted one to publish after a reload
              (#348). */}
          <Route path="/admin/markets/:id/edit" element={<ProtectedRoute requireAdmin><CreateMarketPage /></ProtectedRoute>} />
          <Route path="/admin/markets/:id/propose-outcome" element={<ProtectedRoute requireAdmin><ProposeOutcomePage /></ProtectedRoute>} />
          <Route path="/admin/proposals" element={<ProtectedRoute requireAdmin><ProposalsPage /></ProtectedRoute>} />
          <Route path="/admin/markets/:id/decide-outcome" element={<ProtectedRoute requireAdmin><DecideOutcomePage /></ProtectedRoute>} />
          <Route path="/admin/markets/:id/exposure" element={<ProtectedRoute requireAdmin><MarketExposurePage /></ProtectedRoute>} />
          <Route
            path="/admin/markets/:id/price-history"
            element={
              <ProtectedRoute requireAdmin>
                <Suspense fallback={null}>
                  <MarketPriceHistoryPage />
                </Suspense>
              </ProtectedRoute>
            }
          />
          {/* Last by convention only: React Router ranks a splat below every
              other match wherever it sits. Unguarded, because a guest can
              mistype an address as easily as a trader can. */}
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </AppFrame>
    </BrowserRouter>
  )
}
