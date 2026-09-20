import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import MarketsPage from './pages/MarketsPage'
import RegisterPage from './pages/RegisterPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/markets" element={<ProtectedRoute><MarketsPage /></ProtectedRoute>} />
          {/* /portfolio, /admin — added as built */}
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
