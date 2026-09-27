import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import LandingPage from './LandingPage'

// Stub pages at the places the landing links go. jsdom does not follow a
// plain <a href>, so a stub appears only if the router handled the click
// rather than reloading the whole app.
function renderLandingPage() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<p>login page</p>} />
        <Route path="/register" element={<p>register page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LandingPage', () => {
  it.each([
    ['Log In', 'login page'],
    ['Register', 'register page'],
    [/explore markets/i, 'register page'],
  ])('follows the %s link through the router', async (linkName, destination) => {
    const user = userEvent.setup()
    renderLandingPage()

    await user.click(screen.getByRole('link', { name: linkName }))

    expect(screen.getByText(destination)).toBeInTheDocument()
  })
})
