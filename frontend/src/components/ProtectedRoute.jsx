import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function ProtectedRoute({ children, requireAdmin = false }) {
  const { user } = useAuth()
  const location = useLocation()

  if (user === undefined) return null

  if (!user) return <Navigate to="/login" state={{ from: location }} replace />

  if (requireAdmin && user.role !== 'admin') return <Navigate to="/markets" replace />

  return children
}
