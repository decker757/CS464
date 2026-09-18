import { CSSProperties } from 'react'
import { NAV } from '../../theme/colors'

export const inputBase = (hasError: boolean): CSSProperties => ({
  width: '100%',
  height: 50,
  padding: '0 14px',
  fontSize: 15,
  borderRadius: 10,
  border: `1px solid ${hasError ? '#dc2626' : '#e2e0da'}`,
  backgroundColor: '#fff',
  color: NAV,
  outline: 'none',
  boxSizing: 'border-box',
  transition: 'border-color 0.15s, box-shadow 0.15s',
})

export const focusHandlers = (errors: Record<string, string | undefined>, field: string) => ({
  onFocus: (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = NAV
    e.target.style.boxShadow = '0 0 0 3px rgba(21,30,85,0.08)'
  },
  onBlur: (e: React.FocusEvent<HTMLInputElement>) => {
    e.target.style.borderColor = errors[field] ? '#dc2626' : '#e2e0da'
    e.target.style.boxShadow = 'none'
  },
})
