import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import PageTitle from './PageTitle'

const BASE = "PredictSMU — SMU's prediction market"

beforeEach(() => {
  document.title = BASE
})

describe('PageTitle', () => {
  it('names the browser tab after the heading it renders', () => {
    // The point of titling from here rather than from each page: the tab and
    // the h1 are one string, so they cannot drift apart later (#356). Fourteen
    // pages are titled by this call and by nothing else.
    render(<PageTitle>Portfolio</PageTitle>)

    expect(document.title).toBe('Portfolio · PredictSMU')
  })

  it('leaves the tab alone when the heading is not a plain string', () => {
    // Only a string can be a title. Passing elements straight through would
    // put "[object Object]" in the tab.
    render(<PageTitle><span>Portfolio</span></PageTitle>)

    expect(document.title).toBe(BASE)
  })
})
