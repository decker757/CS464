import { formatPrice } from '../../utils/formatPrice'
import { selectableClass } from '../ui/selectableClass'

// A static, decorative market card for the landing page. None of it is live
// data, and the card says so on its face — a visitor who reads it should not
// come away believing this market or these prices exist.
//
// It is also a picture of the market page, and never more than it: each
// side's price over "implied probability", the chosen side's block filled and
// ticked because the price is what picks the side (#429), then the trading
// panel's one unpriced buy button (#435). The card may show less than the
// page; something reaches the card only after it reaches the page.
//
// The made-up prices are decimal strings, the way the API sends a real one, so
// the card shows them through the app's own formatter instead of inventing a
// second way to write a price. The labels are the create form's defaults, and
// the panel prints a label as it is written, so the button says "Yes"; the
// price block's capitals are its CSS, as on the market page.
const EXAMPLE_OUTCOMES = [
  { label: 'Yes', price: '0.64' },
  { label: 'No', price: '0.36' },
]
// The market page starts on the first outcome, so the picture does too.
const EXAMPLE_CHOSEN_OUTCOME = EXAMPLE_OUTCOMES[0].label

export default function MarketPreviewCard() {
  return (
    <div className="w-full rounded-2xl border border-t-[3px] border-smu-gold/30 border-t-smu-gold bg-white px-6 pt-6 pb-5 shadow-hero">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="rounded-full bg-smu-gold/10 px-[9px] py-[3px] text-[10px] font-bold tracking-[0.6px] text-smu-gold-text uppercase">
          Example
        </span>
        <span className="text-[11px] text-muted">Closes 30 Oct</span>
      </div>

      <p className="mb-4 text-sm leading-[1.45] font-semibold text-smu-navy">
        Will SMU's handball team win SUNIG this year?
      </p>

      {/* The market page's price blocks, as a picture: plain divs rather
          than radios, since there is nothing here to choose, and
          pointer-events-none keeps the unchosen block's hover state from
          firing on a picture. The chosen one takes the page's fill and tick;
          the tick is a character, not the page's icon, because the card
          draws no SVG (MarketPreviewCard.test.tsx). */}
      <div className="pointer-events-none mb-3.5 grid grid-cols-2 gap-2">
        {EXAMPLE_OUTCOMES.map(({ label, price }) => {
          const isChosen = label === EXAMPLE_CHOSEN_OUTCOME
          return (
            <div key={label} className={`relative rounded-xl border px-3 py-3 text-center ${selectableClass(isChosen)}`}>
              <span
                aria-hidden
                className={`absolute top-2 right-2 flex size-3.5 items-center justify-center rounded-full border text-[9px] leading-none ${
                  isChosen ? 'border-smu-gold bg-smu-gold text-smu-navy' : 'border-smu-navy/30'
                }`}
              >
                {isChosen && '✓'}
              </span>
              <p className={`mb-1 text-[10px] font-semibold tracking-[0.5px] uppercase ${isChosen ? 'text-white/80' : 'text-muted'}`}>{label}</p>
              <p className={`text-[22px] leading-none font-extrabold ${isChosen ? 'text-white' : 'text-smu-navy'}`}>{formatPrice(price)}</p>
              <p className={`mt-1 text-[10px] ${isChosen ? 'text-white/75' : 'text-subtle'}`}>implied probability</p>
            </div>
          )
        })}
      </div>

      {/* The trading panel's buy button, as a picture: nothing to click, so
          a plain span rather than a button, and hidden from assistive
          technology so nobody is offered an action this page cannot
          perform. */}
      <span aria-hidden className="mb-4 block rounded-lg bg-smu-navy py-[9px] text-center text-[13px] font-bold text-white">
        Buy {EXAMPLE_CHOSEN_OUTCOME}
      </span>

      <p className="rounded-lg bg-surface-muted px-3 py-[9px] text-xs text-muted">
        An example market. The question and the prices are made up.
      </p>
    </div>
  )
}
