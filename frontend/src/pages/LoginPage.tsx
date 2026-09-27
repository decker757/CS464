import { useState } from 'react'
import * as authApi from '../api/authApi'
import { describeFormError } from '../api/errors'
import AuthFooterLink from '../components/auth/AuthFooterLink'
import AuthLayout from '../components/auth/AuthLayout'
import Button from '../components/ui/Button'
import Field from '../components/ui/Field'
import { Spinner } from '../components/ui/icons'
import PasswordInput from '../components/ui/PasswordInput'
import TextInput from '../components/ui/TextInput'
import { useAuth } from '../context/AuthContext'

type LoginField = 'identifier' | 'password'
type LoginErrors = Partial<Record<LoginField, string>>

const LOGIN_FIELDS: readonly LoginField[] = ['identifier', 'password']

function validateLogin(form: authApi.Credentials): LoginErrors {
  const errors: LoginErrors = {}
  if (!form.identifier.trim()) errors.identifier = 'Enter your username or email.'
  if (!form.password) errors.password = 'Enter your password.'
  return errors
}

export default function LoginPage() {
  const { login } = useAuth()

  const [form, setForm] = useState<authApi.Credentials>({ identifier: '', password: '' })
  const [errors, setErrors] = useState<LoginErrors>({})
  const [serverError, setServerError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
    setErrors((prev) => ({ ...prev, [e.target.name]: undefined }))
    setServerError('')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const validationErrors = validateLogin(form)
    if (Object.keys(validationErrors).length > 0) { setErrors(validationErrors); return }

    setLoading(true)
    try {
      // No navigate() here: GuestOnly sees `user` change and redirects. A
      // second, imperative redirect from here would race it and lose.
      login(await authApi.login(form))
    } catch (err: unknown) {
      const { fieldErrors, formError } = describeFormError(err, {
        fields: LOGIN_FIELDS,
        codeMessages: { invalid_credentials: 'Incorrect username or password.' },
      })
      if (formError) setServerError(formError)
      else setErrors(fieldErrors)
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout
      eyebrow="Welcome back"
      title="Log in to your account"
      subtitle="Enter your username or email and password to continue."
      error={serverError}
      footer={<>Don't have an account? <AuthFooterLink href="/register">Register</AuthFooterLink></>}
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-1">
        <Field id="identifier" label="Username or Email" error={errors.identifier}>
          <TextInput id="identifier" name="identifier" type="text" autoComplete="username" value={form.identifier} onChange={handleChange} placeholder="e.g. michelletan or you@example.com" invalid={!!errors.identifier} />
        </Field>

        <Field id="password" label="Password" error={errors.password}>
          <PasswordInput id="password" name="password" autoComplete="current-password" value={form.password} onChange={handleChange} placeholder="Your password" invalid={!!errors.password} />
        </Field>

        <Button type="submit" variant="primary" size="block" disabled={loading} className="mt-2">
          {loading ? <><Spinner />Logging in…</> : <>Log In<span className="text-base text-smu-gold">→</span></>}
        </Button>
      </form>
    </AuthLayout>
  )
}
