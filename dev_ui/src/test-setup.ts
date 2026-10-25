// Runs before every page test: adds matchers like toBeInTheDocument(), and clears the page and the
// fakes after each test so one test can't leak into the next.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
