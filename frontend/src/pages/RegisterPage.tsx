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
import { RegisterErrors, validateRegister } from '../utils/validate'

const REGISTER_FIELDS = ['username', 'email', 'password'] as const

export default function RegisterPage() {
  const { login } = useAuth()

  const [form, setForm] = useState<authApi.Registration>({ username: '', email: '', password: '' })
  const [errors, setErrors] = useState<RegisterErrors>({})
  const [serverError, setServerError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
    setErrors((prev) => ({ ...prev, [e.target.name]: undefined }))
    setServerError('')
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const validationErrors = validateRegister(form)
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors)
      setServerError('')
      return
    }

    setLoading(true)
    try {
      login(await authApi.register(form)) // GuestOnly owns the redirect, as on LoginPage
    } catch (err: unknown) {
      const { fieldErrors, formError } = describeFormError(err, {
        fields: REGISTER_FIELDS,
        fieldErrorCodes: ['duplicate_user'],
      })
      // Both, always: this response replaces whatever the last one showed.
      setErrors(fieldErrors)
      setServerError(formError)
    } finally {
      setLoading(false)
    }
  }

  return (
    <AuthLayout
      eyebrow="Get Started"
      title="Create your account"
      subtitle="Join PredictSMU and start trading with mock credits."
      error={serverError}
      footer={<>Already have an account? <AuthFooterLink href="/login">Log in</AuthFooterLink></>}
    >
      <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-1">
        <Field id="username" label="Username" error={errors.username}>
          <TextInput id="username" name="username" type="text" autoComplete="username" value={form.username} onChange={handleChange} placeholder="e.g. michelletan" invalid={!!errors.username} />
        </Field>

        <Field id="email" label="Email" error={errors.email}>
          <TextInput id="email" name="email" type="email" autoComplete="email" value={form.email} onChange={handleChange} placeholder="you@example.com" invalid={!!errors.email} />
        </Field>

        <Field id="password" label="Password" error={errors.password} hint="Must be at least 12 characters.">
          <PasswordInput id="password" name="password" autoComplete="new-password" value={form.password} onChange={handleChange} placeholder="Enter at least 12 characters" invalid={!!errors.password} />
        </Field>

        <Button type="submit" variant="primary" size="block" disabled={loading} className="mt-2">
          {loading ? <><Spinner />Creating account…</> : <>Create Account<span className="text-base text-smu-gold">→</span></>}
        </Button>
      </form>
    </AuthLayout>
  )
}
