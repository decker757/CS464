import { selectableClass } from './selectableClass'

// ComponentProps rather than ButtonHTMLAttributes so it carries `ref`, which
// RadioPillGroup needs to move focus between its pills with the arrow keys.
interface FilterPillProps extends React.ComponentProps<'button'> {
  active: boolean
}

/** The rounded pill button shared by a status tab (AdminMarketsPage), the
 * filters drawn by RadioPillGroup (MarketFilters' status, the admin users
 * page's role and status) and a time range (PriceHistoryChart) — same shape,
 * same selected/unselected colours. They differ in ARIA role (a tablist's
 * tabs, a radio group's radios, and toggle buttons with `aria-pressed`), so
 * that stays with the caller via `...props`.
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
