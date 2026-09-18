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

  it('shows error instead of hint when both are provided', () => {
    render(
      <Field id="pw" label="Password" error="Too short." hint="Must be at least 12 characters.">
        <input id="pw" />
      </Field>
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Too short.')
    expect(screen.queryByText('Must be at least 12 characters.')).not.toBeInTheDocument()
  })

  it('renders empty slot (no text) when neither error nor hint is given', () => {
    render(
      <Field id="user" label="Username">
        <input id="user" />
      </Field>
    )
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
