export type Variant = 'primary' | 'gold' | 'outline' | 'outlineOnNavy' | 'outlineGoldOnNavy'
export type Size = 'sm' | 'md' | 'block'

const BASE = 'inline-flex cursor-pointer items-center justify-center gap-2 transition disabled:cursor-not-allowed disabled:opacity-60'

// Hover effects are guarded with `not-disabled:` so a disabled button does not light up.
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-smu-navy text-white not-disabled:hover:ring-3 not-disabled:hover:ring-smu-gold/35',
  gold: 'bg-smu-gold text-white not-disabled:hover:opacity-90',
  outline: 'border-[1.5px] border-smu-navy/20 text-smu-navy not-disabled:hover:border-smu-navy not-disabled:hover:bg-smu-navy/5',
  outlineOnNavy: 'border border-white/35 text-white not-disabled:hover:border-white/60 not-disabled:hover:bg-white/10',
  outlineGoldOnNavy: 'border border-smu-gold text-white/85 not-disabled:hover:bg-smu-gold/20',
}

const SIZES: Record<Size, string> = {
  sm: 'rounded-lg px-[18px] py-2 text-sm font-medium',
  md: 'rounded-control px-[26px] py-[13px] text-[15px] font-semibold',
  block: 'h-[50px] w-full rounded-control text-[15px] font-bold',
}

/** The classes for a button. Use it directly on an `<a>` or `<Link>` that should look like one. */
export function buttonClass(variant: Variant, size: Size) {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]}`
}
