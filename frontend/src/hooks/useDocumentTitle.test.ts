import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useDocumentTitle } from './useDocumentTitle'

const BASE = "PredictSMU — SMU's prediction market"

beforeEach(() => {
  document.title = BASE
})

describe('useDocumentTitle', () => {
  it('names the page in the tab, alongside the app', () => {
    // index.html gives every tab, bookmark and shared link the same title;
    // the page's own name is what tells two open tabs apart (#356).
    renderHook(() => useDocumentTitle('Portfolio'))

    expect(document.title).toBe('Portfolio · PredictSMU')
  })

  it('puts the title back when the page goes away', () => {
    const { unmount } = renderHook(() => useDocumentTitle('Portfolio'))

    unmount()

    expect(document.title).toBe(BASE)
  })

  it('leaves the title alone until there is a name to show', () => {
    // A market detail page knows its question only after the fetch lands.
    // Without this the tab reads " · PredictSMU" for the whole round trip.
    renderHook(() => useDocumentTitle(null))

    expect(document.title).toBe(BASE)
  })
})
