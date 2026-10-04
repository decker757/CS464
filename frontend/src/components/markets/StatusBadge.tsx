import Badge from '../ui/Badge'
import { statusDisplay } from './marketStatus'

export default function StatusBadge({ status }: { status: string }) {
  const { label, tone } = statusDisplay(status)
  return <Badge tone={tone}>{label}</Badge>
}
