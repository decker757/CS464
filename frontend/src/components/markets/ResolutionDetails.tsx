import type { PublicMarketDetail } from '../../api/marketApi'
import DetailItem from '../ui/DetailItem'
import { formatDateTime } from '../../utils/formatDateTime'
import { tradingStopped } from './marketStatus'

interface ResolutionDetailsProps {
  market: Pick<PublicMarketDetail, 'status' | 'resolution_criteria' | 'resolution_sources' | 'close_time' | 'resolution_time'>
}

// What the close date is called. Once trading has stopped it is the schedule
// the market was published with rather than a close still to come: an admin's
// early close leaves close_time in the future (market-service.md), and the
// page above already says "Closed". Stopped is the clock rule (ADR 0011), not
// the status column alone (#429).
function closeDateLabel(market: ResolutionDetailsProps['market']): string {
  return tradingStopped(market.status, market.close_time) ? 'Scheduled to close' : 'Closes'
}

/** The rules a market resolves by: the criteria, when it closes and resolves,
 * and the sources as links. Shown to a trader before they trade and to the
 * second administrator deciding a proposed outcome — the two-person model
 * rests on those being the same rules, so they are the same component. */
export default function ResolutionDetails({ market }: ResolutionDetailsProps) {
  return (
    <div className="flex flex-col gap-4 text-sm text-smu-navy">
      <DetailItem label="Resolution criteria">
        <p className="leading-[1.6]">{market.resolution_criteria}</p>
      </DetailItem>
      <div className="grid grid-cols-2 gap-4">
        <DetailItem label={closeDateLabel(market)}>
          <p>{formatDateTime(market.close_time)}</p>
        </DetailItem>
        <DetailItem label="Resolves by">
          <p>{formatDateTime(market.resolution_time)}</p>
        </DetailItem>
      </div>
      {market.resolution_sources.length > 0 && (
        <DetailItem label="Resolution sources">
          <ul className="mt-1 flex flex-col gap-1">
            {market.resolution_sources.map(source => (
              <li key={source.id}>
                <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-info">
                  {source.label ?? source.url}
                </a>
              </li>
            ))}
          </ul>
        </DetailItem>
      )}
    </div>
  )
}
