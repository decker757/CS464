const USERNAME_RE = /^[A-Za-z0-9_-]+$/

export interface RegisterForm {
  username: string
  email: string
  password: string
}

export interface RegisterErrors {
  username?: string
  email?: string
  password?: string
}

export function validateRegister(form: RegisterForm): RegisterErrors {
  const errors: RegisterErrors = {}

  if (form.username.length < 3 || form.username.length > 32)
    errors.username = 'Username must be 3–32 characters.'
  else if (!USERNAME_RE.test(form.username))
    errors.username = 'Username may only contain letters, numbers, hyphens and underscores.'

  if (!form.email.includes('@'))
    errors.email = 'Enter a valid email address.'

  if (form.password.length < 12)
    errors.password = 'Password must be at least 12 characters.'

  return errors
}
