import Badge from '../ui/Badge'
import { statusDisplay, type StatusAudience } from './marketStatus'

interface StatusBadgeProps {
  status: string
  /** Whose words the status is shown in; an administrator's by default (marketStatus.ts). */
  audience?: StatusAudience
}

export default function StatusBadge({ status, audience }: StatusBadgeProps) {
  const { label, tone } = statusDisplay(status, audience)
  return <Badge tone={tone}>{label}</Badge>
}
