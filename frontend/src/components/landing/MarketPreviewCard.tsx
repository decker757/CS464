import { formatPrice } from '../../utils/formatPrice'
import { selectableClass } from '../ui/selectableClass'

// A static, decorative market card for the landing page. None of it is live
// data, and the card says so on its face — a visitor who reads it should not
// come away believing this market or these prices exist.
//
// It is also a picture of the market page, and never more than it: each
// side's price over "implied probability", then the trading panel's outcome
// toggle and its one unpriced buy button (#435). The card may show less than
// the page; something reaches the card only after it reaches the page.
//
// The made-up prices are decimal strings, the way the API sends a real one, so
// the card shows them through the app's own formatter instead of inventing a
// second way to write a price. The labels are the create form's defaults, and
// the panel prints a label as it is written, so the toggle and the button say
// "Yes"; the price tile's capitals are its CSS, as on the market page.
const EXAMPLE_OUTCOMES = [
  { label: 'Yes', price: '0.64' },
  { label: 'No', price: '0.36' },
]
// The trading panel starts on the first outcome, so the picture does too.
const EXAMPLE_CHOSEN_OUTCOME = EXAMPLE_OUTCOMES[0].label

export default function MarketPreviewCard() {
  return (
    <div className="w-full rounded-2xl border border-t-[3px] border-smu-gold/30 border-t-smu-gold bg-white px-6 pt-6 pb-5 shadow-hero">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="rounded-full bg-smu-gold/10 px-[9px] py-[3px] text-[10px] font-bold tracking-[0.6px] text-smu-gold uppercase">
          Example
        </span>
        <span className="text-[11px] text-muted">Closes 30 Oct</span>
      </div>

      <p className="mb-4 text-sm leading-[1.45] font-semibold text-smu-navy">
        Will SMU's handball team win SUNIG this year?
      </p>

      <div className="mb-3.5 grid grid-cols-2 gap-2">
        {EXAMPLE_OUTCOMES.map(({ label, price }) => (
          <div key={label} className="rounded-xl bg-smu-cream px-3 py-3 text-center">
            <p className="mb-1 text-[10px] font-semibold tracking-[0.5px] text-muted uppercase">{label}</p>
            <p className="text-[22px] leading-none font-extrabold text-smu-navy">{formatPrice(price)}</p>
            <p className="mt-1 text-[10px] text-subtle">implied probability</p>
          </div>
        ))}
      </div>

      {/* The trading panel's outcome toggle and buy button, as a picture:
          nothing to click, so they are plain spans rather than buttons, and
          hidden from assistive technology so nobody is offered an action this
          page cannot perform. The toggle takes the panel's own colours, and
          pointer-events-none keeps their one hover state from firing on a
          picture. */}
      <div aria-hidden className="pointer-events-none mb-4 text-[13px]">
        <div className="mb-2 flex gap-2">
          {EXAMPLE_OUTCOMES.map(({ label }) => {
            const isChosen = label === EXAMPLE_CHOSEN_OUTCOME
            return (
              <span
                key={label}
                className={`flex-1 rounded-lg border py-[7px] text-center font-medium ${selectableClass(isChosen)}`}
              >
                {label}
              </span>
            )
          })}
        </div>
        <span className="block rounded-lg bg-smu-navy py-[9px] text-center font-bold text-white">
          Buy {EXAMPLE_CHOSEN_OUTCOME}
        </span>
      </div>

      <p className="rounded-lg bg-surface-muted px-3 py-[9px] text-xs text-muted">
        An example market. The question and the prices are made up.
      </p>
    </div>
  )
}
