import { cloneElement, isValidElement } from 'react'

interface FieldProps {
  id: string
  label: string
  error?: string
  hint?: string
  children: React.ReactNode
}

/** A labelled control with one message under it: the error when there is one,
 * otherwise the hint. The message is tied to the control with
 * `aria-describedby`, so a screen reader reads it whenever the field takes
 * focus and not only once, as the alert goes past.
 *
 * `children` must be a single element that spreads its props onto a DOM node —
 * `TextInput`, `TextArea` and `PasswordInput` all do. A fragment, or a
 * component that drops the props it is not named, swallows the attribute
 * silently: the clone still succeeds and TypeScript still passes, because
 * `isValidElement<P>` asserts the prop shape rather than checking it. */
export default function Field({ id, label, error, hint, children }: FieldProps) {
  const message = error ?? hint
  const messageId = `${id}-message`
  // Described only while there is a message to read: pointing at the empty
  // paragraph below would describe the field with nothing.
  const control =
    message && isValidElement<{ 'aria-describedby'?: string }>(children)
      ? cloneElement(children, { 'aria-describedby': messageId })
      : children

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[13px] font-semibold text-smu-navy">
        {label}
      </label>
      {control}
      {/* Always rendered, so the card does not jump when a message appears. */}
      <p id={messageId} role={error ? 'alert' : undefined} className={`min-h-[18px] text-xs ${error ? 'text-danger' : 'text-subtle'}`}>
        {message ?? ''}
      </p>
    </div>
  )
}
