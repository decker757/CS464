import { NAV } from '../../theme/colors'

export default function Field({ id, label, error, hint, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label htmlFor={id} style={{ fontSize: 13, fontWeight: 600, color: NAV }}>
        {label}
      </label>
      {children}
      {/* Reserved slot — always occupies space to prevent card jump */}
      <p
        role={error ? 'alert' : undefined}
        style={{ fontSize: 12, minHeight: 18, color: error ? '#dc2626' : '#9ca3af', margin: 0 }}
      >
        {error ?? hint ?? ''}
      </p>
    </div>
  )
}
