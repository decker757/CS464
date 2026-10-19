import type { PublicMarketDetail } from '../../api/marketApi'
import DetailItem from '../ui/DetailItem'
import { formatDateTime } from '../../utils/formatDateTime'

interface ResolutionDetailsProps {
  market: Pick<PublicMarketDetail, 'resolution_criteria' | 'resolution_sources' | 'close_time' | 'resolution_time'>
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
        <DetailItem label="Closes">
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
