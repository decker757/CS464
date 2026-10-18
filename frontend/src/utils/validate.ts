import type { Registration } from '../api/authApi'

const USERNAME_RE = /^[A-Za-z0-9_-]+$/
const USERNAME_MIN = 3
const USERNAME_MAX = 32
const PASSWORD_MIN = 12

/** The rules, worded for the form, so a field can state what it accepts
 * before the user submits and the numbers are written down once. */
export const USERNAME_HINT = `${USERNAME_MIN}–${USERNAME_MAX} characters: letters, numbers, hyphens and underscores.`
export const PASSWORD_HINT = `Must be at least ${PASSWORD_MIN} characters.`

export type RegisterForm = Registration

export interface RegisterErrors {
  username?: string
  email?: string
  password?: string
}

export function validateRegister(form: RegisterForm): RegisterErrors {
  const errors: RegisterErrors = {}
  const username = form.username.trim()

  if (username.length < USERNAME_MIN || username.length > USERNAME_MAX)
    errors.username = `Username must be ${USERNAME_MIN}–${USERNAME_MAX} characters.`
  else if (!USERNAME_RE.test(username))
    errors.username = 'Username may only contain letters, numbers, hyphens and underscores.'

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim()))
    errors.email = 'Enter a valid email address.'

  if (form.password.length < PASSWORD_MIN)
    errors.password = `Password must be at least ${PASSWORD_MIN} characters.`

  return errors
}
