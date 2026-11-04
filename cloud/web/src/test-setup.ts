// Runs before every page test: adds matchers like toBeInTheDocument(), and clears the page, the fakes and
// the browser's storage after each test so one test can't leak into the next.
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import { clearSeen } from './seen'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
  window.location.hash = ''
  clearSeen()
})
