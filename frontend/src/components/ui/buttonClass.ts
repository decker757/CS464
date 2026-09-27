export type Variant = 'primary'
export type Size = 'block'

const BASE = 'inline-flex cursor-pointer items-center justify-center gap-2 transition disabled:cursor-not-allowed disabled:opacity-60'

// Hover effects are guarded with `not-disabled:` so a disabled button does not light up.
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-smu-navy text-white not-disabled:hover:ring-3 not-disabled:hover:ring-smu-gold/35',
}

const SIZES: Record<Size, string> = {
  block: 'h-[50px] w-full rounded-control text-[15px] font-bold',
}

/** The classes for a button. Use it directly on an `<a>` or `<Link>` that should look like one. */
export function buttonClass(variant: Variant, size: Size) {
  return `${BASE} ${VARIANTS[variant]} ${SIZES[size]}`
}
