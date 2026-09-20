import axios from 'axios'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import api from '../api/axios'
import { ApiError, FastApiError } from '../api/errors'
import { EyeIcon, Spinner, TrendIcon } from '../components/auth/AuthIcons'
import BrandPanel from '../components/auth/BrandPanel'
import Field from '../components/auth/Field'
import { focusHandlers, inputBase } from '../components/auth/inputStyles'
import { User, useAuth } from '../context/AuthContext'
import { GOLD, NAV } from '../theme/colors'

interface LoginErrors { identifier?: string; password?: string }

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/markets'

  useEffect(() => {
    if (user) navigate('/markets', { replace: true })
  }, [user, navigate])

  const [form, setForm] = useState({ identifier: '', password: '' })
  const [errors, setErrors] = useState<LoginErrors>({})
  const [serverError, setServerError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

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
      login(res.data.user)
      navigate(from, { replace: true })
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
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <BrandPanel />

      <div style={{ flex: 1, backgroundColor: '#FAF8F3', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 'clamp(24px, 4vw, 48px) clamp(20px, 5vw, 48px)' }}>
        <a href="/" className="flex md:hidden" style={{ alignItems: 'center', gap: 8, textDecoration: 'none', marginBottom: 28 }}>
          <TrendIcon size={18} color={GOLD} />
          <span style={{ color: NAV, fontSize: 18, fontWeight: 700 }}>PredictSMU</span>
        </a>

        <div style={{ width: '100%', maxWidth: 460, backgroundColor: '#fff', border: '1px solid rgba(182,145,70,0.2)', borderRadius: 20, boxShadow: '0 4px 24px rgba(21,30,85,0.07)', padding: 'clamp(24px, 4vw, 36px)' }}>
          <a href="/" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 13, color: '#9ca3af', textDecoration: 'none', marginBottom: 20, transition: 'color 0.15s' }} onMouseEnter={(e) => (e.currentTarget.style.color = NAV)} onMouseLeave={(e) => (e.currentTarget.style.color = '#9ca3af')}>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
              <polyline points="9,2 4,7 9,12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Back to home
          </a>

          <span style={{ fontSize: 11, fontWeight: 700, color: GOLD, textTransform: 'uppercase', letterSpacing: '1px', display: 'block', marginBottom: 6 }}>Welcome back</span>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: NAV, marginBottom: 4, letterSpacing: '-0.3px' }}>Log in to your account</h1>
          <p style={{ fontSize: 14, color: '#6b7280', marginBottom: 20, lineHeight: 1.5 }}>Enter your username or email and password to continue.</p>

          {serverError && (
            <div role="alert" style={{ padding: '11px 14px', borderRadius: 10, backgroundColor: '#fef2f2', border: '1px solid #fecaca', color: '#dc2626', fontSize: 13, marginBottom: 16 }}>
              {serverError}
            </div>
          )}

          <form onSubmit={handleSubmit} noValidate style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Field id="identifier" label="Username or Email" error={errors.identifier}>
              <input id="identifier" name="identifier" type="text" autoComplete="username" value={form.identifier} onChange={handleChange} placeholder="e.g. michelletan or you@example.com" style={inputBase(!!errors.identifier)} {...focusHandlers(!!errors.identifier)} />
            </Field>

            <Field id="password" label="Password" error={errors.password}>
              <div style={{ position: 'relative' }}>
                <input id="password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={form.password} onChange={handleChange} placeholder="Your password" style={{ ...inputBase(!!errors.password), paddingRight: 44 }} {...focusHandlers(!!errors.password)} />
                <button type="button" aria-label={showPassword ? 'Hide password' : 'Show password'} onClick={() => setShowPassword((v) => !v)} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', display: 'flex', alignItems: 'center', padding: 4, borderRadius: 4, transition: 'color 0.15s' }} onMouseEnter={(e) => (e.currentTarget.style.color = NAV)} onMouseLeave={(e) => (e.currentTarget.style.color = '#9ca3af')}>
                  <EyeIcon open={showPassword} />
                </button>
              </div>
            </Field>

            <button type="submit" disabled={loading} style={{ width: '100%', height: 50, backgroundColor: NAV, color: '#fff', fontWeight: 700, fontSize: 15, borderRadius: 10, border: 'none', cursor: loading ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, marginTop: 8, opacity: loading ? 0.75 : 1, transition: 'opacity 0.15s, box-shadow 0.15s' }} onMouseEnter={(e) => { if (!loading) e.currentTarget.style.boxShadow = '0 0 0 3px rgba(182,145,70,0.35)' }} onMouseLeave={(e) => { e.currentTarget.style.boxShadow = 'none' }}>
              {loading ? <><Spinner />Logging in…</> : <>Log In<span style={{ color: GOLD, fontSize: 16 }}>→</span></>}
            </button>
          </form>

          <p style={{ textAlign: 'center', fontSize: 14, color: '#6b7280', marginTop: 20 }}>
            Don't have an account?{' '}
            <a href="/register" style={{ color: NAV, fontWeight: 600, textDecoration: 'none', borderBottom: `1px solid rgba(21,30,85,0.25)` }} onMouseEnter={(e) => (e.currentTarget.style.borderBottomColor = NAV)} onMouseLeave={(e) => (e.currentTarget.style.borderBottomColor = 'rgba(21,30,85,0.25)')}>
              Register
            </a>
          </p>
        </div>
      </div>
    </div>
  )
}
