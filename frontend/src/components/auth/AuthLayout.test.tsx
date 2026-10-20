import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import AuthLayout from './AuthLayout'

const BASE = "PredictSMU — SMU's prediction market"

beforeEach(() => {
  document.title = BASE
})

describe('AuthLayout', () => {
  it('names the browser tab after the page it frames', () => {
    // Login and register have no PageTitle — this frame renders their h1 — so
    // this call is the only thing that titles either of them (#356).
    render(
      <MemoryRouter>
        <AuthLayout
          eyebrow="Welcome back"
          title="Log in to your account"
          subtitle="Enter your username or email and password to continue."
          footer={null}
        >
          <form />
        </AuthLayout>
      </MemoryRouter>,
    )

    expect(document.title).toBe('Log in to your account · PredictSMU')
  })
})
