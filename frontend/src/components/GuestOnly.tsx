import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import LoadingScreen from './LoadingScreen'

interface GuestOnlyProps {
  children: React.ReactNode
}

export default function GuestOnly({ children }: GuestOnlyProps) {
  const { user } = useAuth()

  if (user === undefined) return <LoadingScreen />

  if (user) return <Navigate to="/markets" replace />

  return <>{children}</>
}
