/**
 * The small gold uppercase label above a heading. Pass size and margin in `className`.
 * It is 11px, so it takes the darker text gold rather than the brand gold. [#360]
 */
export default function Eyebrow({ className = '', children }: { className?: string; children: React.ReactNode }) {
  return <p className={`font-bold tracking-[1px] text-smu-gold-text uppercase ${className}`}>{children}</p>
}
