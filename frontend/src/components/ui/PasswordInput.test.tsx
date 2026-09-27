import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import PasswordInput from './PasswordInput'

// Controlled, the way the login and register pages use it.
function PasswordField() {
  const [password, setPassword] = useState('')
  return (
    <>
      <label htmlFor="password">Password</label>
      <PasswordInput id="password" value={password} onChange={(e) => setPassword(e.target.value)} />
    </>
  )
}

describe('PasswordInput', () => {
  it('shows and hides the password without losing what was typed', async () => {
    const user = userEvent.setup()
    render(<PasswordField />)
    const input = screen.getByLabelText('Password')

    await user.type(input, 'test-fixture-pw-ok')
    expect(input).toHaveAttribute('type', 'password')

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'text')
    expect(input).toHaveValue('test-fixture-pw-ok')

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(input).toHaveAttribute('type', 'password')
    expect(input).toHaveValue('test-fixture-pw-ok')
  })
})
