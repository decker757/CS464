import { selectableClass } from './selectableClass'

interface FilterPillProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active: boolean
}

/** The rounded pill button shared by a status tab (AdminMarketsPage) and a
 * status filter (MarketFilters) — same shape, same selected/unselected
 * colours. The two differ in ARIA role (a tablist's tabs vs. a filter
 * group's toggle buttons), so that stays with the caller via `...props`. */
export default function FilterPill({ active, className = '', ...props }: FilterPillProps) {
  return (
    <button
      type="button"
      {...props}
      className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition ${selectableClass(active)} ${className}`}
    />
  )
}
