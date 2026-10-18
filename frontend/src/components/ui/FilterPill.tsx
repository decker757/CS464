import { selectableClass } from './selectableClass'

interface FilterPillProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  active: boolean
}

/** The rounded pill button shared by a status tab (AdminMarketsPage) and a
 * status filter (MarketFilters) — same shape, same selected/unselected
 * colours. The two differ in ARIA role (a tablist's tabs vs. a filter
 * group's toggle buttons), so that stays with the caller via `...props`.
 *
 * `pointer-coarse:` grows the pill to the 44px a finger needs, and only on a
 * device driven by one: six pills sit in one wrapping row on the browse page's
 * primary control, and at 28px they are a row of near-misses ([X-1] #364). A
 * mouse hits 28px fine, so the desktop pill is left as it was. */
export default function FilterPill({ active, className = '', ...props }: FilterPillProps) {
  return (
    <button
      type="button"
      {...props}
      className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition pointer-coarse:min-h-11 pointer-coarse:px-5 ${selectableClass(active)} ${className}`}
    />
  )
}
