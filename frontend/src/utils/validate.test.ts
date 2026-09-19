import { describe, expect, it } from 'vitest'
import { validateRegister } from './validate'

describe('validateRegister', () => {
  const valid = {
    username: 'alice',
    email: 'alice@smu.edu.sg',
    password: 'test-fixture-pw-ok',
  }

  it('returns no errors for a valid form', () => {
    expect(validateRegister(valid)).toEqual({})
  })

  // Username length — test exactly at each boundary
  it('rejects username of 2 characters', () => {
    expect(validateRegister({ ...valid, username: 'ab' }).username)
      .toBe('Username must be 3–32 characters.')
  })

  it('accepts username of exactly 3 characters', () => {
    expect(validateRegister({ ...valid, username: 'abc' })).toEqual({})
  })

  it('accepts username of exactly 32 characters', () => {
    expect(validateRegister({ ...valid, username: 'a'.repeat(32) })).toEqual({})
  })

  it('rejects username of 33 characters', () => {
    expect(validateRegister({ ...valid, username: 'a'.repeat(33) }).username)
      .toBe('Username must be 3–32 characters.')
  })

  it('rejects username containing spaces', () => {
    expect(validateRegister({ ...valid, username: 'alice tan' }).username)
      .toBe('Username may only contain letters, numbers, hyphens and underscores.')
  })

  it('accepts hyphens and underscores in username', () => {
    expect(validateRegister({ ...valid, username: 'alice_tan-2' })).toEqual({})
  })

  it('trims leading and trailing spaces before checking username', () => {
    // server also trims; form should not block a name that would be valid after trim
    expect(validateRegister({ ...valid, username: ' alice ' })).toEqual({})
  })

  // Email
  it('rejects email with no @', () => {
    expect(validateRegister({ ...valid, email: 'notanemail' }).email)
      .toBe('Enter a valid email address.')
  })

  it('rejects bare a@b (no domain dot)', () => {
    expect(validateRegister({ ...valid, email: 'a@b' }).email)
      .toBe('Enter a valid email address.')
  })

  it('accepts a well-formed email', () => {
    expect(validateRegister({ ...valid, email: 'test@smu.edu.sg' })).toEqual({})
  })

  // Password length — test exactly at each boundary
  it('rejects password of 11 characters', () => {
    expect(validateRegister({ ...valid, password: 'a'.repeat(11) }).password)
      .toBe('Password must be at least 12 characters.')
  })

  it('accepts password of exactly 12 characters', () => {
    expect(validateRegister({ ...valid, password: 'a'.repeat(12) })).toEqual({})
  })

  it('accumulates all three errors at once', () => {
    const errors = validateRegister({ username: 'x', email: 'bad', password: 'tiny' })
    expect(errors.username).toBeTruthy()
    expect(errors.email).toBeTruthy()
    expect(errors.password).toBeTruthy()
  })
})
