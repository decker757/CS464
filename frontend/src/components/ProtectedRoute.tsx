import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
  requireAdmin?: boolean
}

export default function ProtectedRoute({ children, requireAdmin = false }: ProtectedRouteProps) {
  const { user } = useAuth()
  const location = useLocation()

  if (user === undefined) return null

  if (!user) return <Navigate to="/login" state={{ from: location }} replace />

  if (requireAdmin && user.role !== 'admin') return <Navigate to="/markets" replace />

  return <>{children}</>
}
