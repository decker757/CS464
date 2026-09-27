interface FieldProps {
  id: string
  label: string
  error?: string
  hint?: string
  children: React.ReactNode
}

export default function Field({ id, label, error, hint, children }: FieldProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-semibold text-smu-navy">
        {label}
      </label>
      {children}
      {/* Always rendered, so the card does not jump when a message appears. */}
      <p role={error ? 'alert' : undefined} className={`min-h-[18px] text-xs ${error ? 'text-danger' : 'text-subtle'}`}>
        {error ?? hint ?? ''}
      </p>
    </div>
  )
}
