export type Variant = 'primary' | 'gold' | 'outline' | 'outlineOnNavy' | 'outlineGoldOnNavy' | 'dashed'
export type Size = 'xs' | 'sm' | 'md' | 'lg' | 'block'

const BASE = 'inline-flex cursor-pointer items-center justify-center gap-2 transition disabled:cursor-not-allowed disabled:opacity-60'

// Hover effects are guarded with `not-disabled:` so a disabled button does not light up.
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-smu-navy text-white not-disabled:hover:ring-3 not-disabled:hover:ring-smu-gold/35',
  // Navy on the brand gold is 4.58:1, white on it 3.40:1, so the label is navy
  // and the gold is untouched. The hover lightens the surface instead of the
  // whole button, which would have faded the label with it. That hover is
  // translucent, so this variant wants a light page: over navy it lets the
  // navy through and falls to 3.96:1. Both callers today sit on cream. [#360]
  gold: 'bg-smu-gold text-smu-navy not-disabled:hover:bg-smu-gold/90',
  outline: 'border-[1.5px] border-smu-navy/20 text-smu-navy not-disabled:hover:border-smu-navy not-disabled:hover:bg-smu-navy/5',
  outlineOnNavy: 'border border-white/35 text-white not-disabled:hover:border-white/60 not-disabled:hover:bg-white/10',
  outlineGoldOnNavy: 'border border-smu-gold text-white/85 not-disabled:hover:bg-smu-gold/20',
  dashed: 'border border-dashed border-smu-navy/30 text-smu-navy',
}

const SIZES: Record<Size, string> = {
  xs: 'rounded-lg px-3.5 py-1.5 text-[13px]',
  sm: 'rounded-lg px-[18px] py-2 text-sm font-medium',
  md: 'rounded-control px-[26px] py-[13px] text-[15px] font-semibold',
  lg: 'rounded-control px-8 py-3.5 text-[15px] font-semibold',
  block: 'h-[50px] w-full rounded-control text-[15px] font-bold',
}

/** The classes for a button. Use it directly on an `<a>` or `<Link>` that should look like one. */
export function buttonClass(variant: Variant, size: Size) {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]}`
}
