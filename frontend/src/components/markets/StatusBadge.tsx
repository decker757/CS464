import Badge from '../ui/Badge'
import { type MarketStatus, STATUS_CONFIG } from './marketStatus'

export default function StatusBadge({ status }: { status: MarketStatus }) {
  const { label, tone } = STATUS_CONFIG[status]
  return <Badge tone={tone}>{label}</Badge>
}
