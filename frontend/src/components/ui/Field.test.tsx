import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import Field from './Field'

describe('Field', () => {
  it('renders the label and children', () => {
    render(
      <Field id="email" label="Email">
        <input id="email" />
      </Field>
    )
    expect(screen.getByText('Email')).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })

  it('shows error message with alert role when error prop provided', () => {
    render(
      <Field id="email" label="Email" error="Enter a valid email address.">
        <input id="email" />
      </Field>
    )
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Enter a valid email address.')
    // Tied to the input too: the alert is read once as it appears, and this is
    // what makes the reason read again every time the field takes focus.
    expect(screen.getByRole('textbox')).toHaveAccessibleDescription('Enter a valid email address.')
  })

  it('shows hint when no error is present', () => {
    render(
      <Field id="pw" label="Password" hint="Must be at least 12 characters.">
        <input id="pw" />
      </Field>
    )
    expect(screen.getByText('Must be at least 12 characters.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // A hint has no alert role, so `aria-describedby` is the only way it is ever
  // announced. Without the wiring it is text a screen-reader user never hears.
  it('describes the input with the hint', () => {
    render(
      <Field id="pw" label="Password" hint="Must be at least 12 characters.">
        <input id="pw" />
      </Field>
    )
    expect(screen.getByRole('textbox')).toHaveAccessibleDescription('Must be at least 12 characters.')
  })

  it('shows error instead of hint when both are provided', () => {
    render(
      <Field id="pw" label="Password" error="Too short." hint="Must be at least 12 characters.">
        <input id="pw" />
      </Field>
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Too short.')
    expect(screen.queryByText('Must be at least 12 characters.')).not.toBeInTheDocument()
  })
})
