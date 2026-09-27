/** The small gold uppercase label above a heading. Pass size and margin in `className`. */
export default function Eyebrow({ className = '', children }: { className?: string; children: React.ReactNode }) {
  return <p className={`font-bold tracking-[1px] text-smu-gold uppercase ${className}`}>{children}</p>
}
