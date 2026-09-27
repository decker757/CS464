import { useState } from 'react'
import { EyeIcon } from './icons'
import TextInput, { type TextInputProps } from './TextInput'

/** A text input with a show/hide toggle. Owns its own visibility state. */
export default function PasswordInput({ className = '', ...props }: Omit<TextInputProps, 'type'>) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="relative">
      <TextInput {...props} type={visible ? 'text' : 'password'} className={`pr-11 ${className}`} />
      <button
        type="button"
        aria-label={visible ? 'Hide password' : 'Show password'}
        onClick={() => setVisible((v) => !v)}
        className="absolute top-1/2 right-3 flex -translate-y-1/2 cursor-pointer items-center rounded p-1 text-subtle transition-colors hover:text-smu-navy"
      >
        <EyeIcon open={visible} />
      </button>
    </div>
  )
}
