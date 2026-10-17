/** A labelled value: a small uppercase label above whatever content follows it. */
export default function DetailItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold tracking-[0.5px] text-subtle uppercase">{label}</p>
      {children}
    </div>
  )
}
