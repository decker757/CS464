import { describe, expect, it } from 'vitest'
import { validateRegister } from './validate'

describe('validateRegister', () => {
  const valid = { username: 'alice', email: 'alice@smu.edu.sg', password: 'test-fixture-pw-ok' }

  it('returns no errors for a valid form', () => {
    expect(validateRegister(valid)).toEqual({})
  })

  it('errors when username is shorter than 3 chars', () => {
    const { username } = validateRegister({ ...valid, username: 'ab' })
    expect(username).toMatch(/3/)
  })

  it('errors when username is longer than 32 chars', () => {
    const { username } = validateRegister({ ...valid, username: 'a'.repeat(33) })
    expect(username).toMatch(/32/)
  })

  it('errors when username contains invalid characters', () => {
    const { username } = validateRegister({ ...valid, username: 'alice smith' })
    expect(username).toBeTruthy()
  })

  it('allows hyphens and underscores in username', () => {
    expect(validateRegister({ ...valid, username: 'alice_tan-2' })).toEqual({})
  })

  it('errors when email has no @', () => {
    const { email } = validateRegister({ ...valid, email: 'notanemail' })
    expect(email).toBeTruthy()
  })

  it('errors when password is shorter than 12 characters', () => {
    const { password } = validateRegister({ ...valid, password: 'short' })
    expect(password).toMatch(/12/)
  })

  it('accumulates multiple errors at once', () => {
    const errors = validateRegister({ username: 'x', email: 'bad', password: 'tiny' })
    expect(errors.username).toBeTruthy()
    expect(errors.email).toBeTruthy()
    expect(errors.password).toBeTruthy()
  })
})
