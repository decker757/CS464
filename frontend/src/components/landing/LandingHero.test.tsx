import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import LandingHero from './LandingHero'

describe('LandingHero', () => {
  // The standings used to rank the team's own handles against made-up dollar
  // amounts, which read as a real leaderboard (#366).
  it('ranks placeholder handles, in credits', () => {
    render(
      <MemoryRouter>
        <LandingHero />
      </MemoryRouter>,
    )

    expect(screen.getByText('trader_one')).toBeInTheDocument()
    expect(screen.queryByText('ernest_t')).not.toBeInTheDocument()
    expect(screen.getByText('2,340 credits')).toBeInTheDocument()
    expect(screen.queryAllByText(/[$¢]/)).toHaveLength(0)
  })
})
