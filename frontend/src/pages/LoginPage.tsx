import axios from 'axios'
import { useState } from 'react'
import api from '../api/axios'
import { ApiError, FastApiError } from '../api/errors'
import AuthFooterLink from '../components/auth/AuthFooterLink'
import AuthLayout from '../components/auth/AuthLayout'
import Button from '../components/ui/Button'
import Field from '../components/ui/Field'
import { Spinner } from '../components/ui/icons'
import PasswordInput from '../components/ui/PasswordInput'
import TextInput from '../components/ui/TextInput'
import { User, useAuth } from '../context/AuthContext'

interface LoginErrors { identifier?: string; password?: string }

export default function LoginPage() {
  const { login } = useAuth()

  const [form, setForm] = useState({ identifier: '', password: '' })
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
    const e2: LoginErrors = {}
    if (!form.identifier.trim()) e2.identifier = 'Enter your username or email.'
    if (!form.password) e2.password = 'Enter your password.'
    if (Object.keys(e2).length > 0) { setErrors(e2); return }

    setLoading(true)
    try {
      const res = await api.post<{ user: User }>('/auth/login', form)
      // No navigate() here. `login` sets `user`, and GuestOnly — which wraps
      // this route in App.tsx — reads that and redirects to wherever the
      // visitor was headed. It re-renders before an imperative call from here
      // could land, so adding one back does not add a fallback; it adds a
      // second authority that this guard then overwrites.
      login(res.data.user)
    } catch (err: unknown) {
      if (!axios.isAxiosError(err)) { setServerError('Something went wrong. Please try again.'); return }
      const data = err.response?.data as (ApiError & FastApiError) | undefined
      if (data?.detail?.length) {
        // FastAPI 422: pydantic rejected a field
        const first = data.detail[0]
        const field = first.loc[first.loc.length - 1] as string
        if (field === 'identifier' || field === 'password') {
          setErrors({ [field]: first.msg })
        } else {
          setServerError(first.msg || 'Something went wrong. Please try again.')
        }
      } else if (data?.error?.code === 'invalid_credentials') {
        setServerError('Incorrect username or password.')
      } else if (data?.error?.message) {
        setServerError(data.error.message)
      } else {
        setServerError('Something went wrong. Please try again.')
      }
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
