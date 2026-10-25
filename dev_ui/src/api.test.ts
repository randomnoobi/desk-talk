import { describe, expect, it, vi } from 'vitest'
import { ApiError, getJSON, getTrace, postJSON, startTests } from './api'

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function fakeFetch(...replies: Response[]) {
  const fetch = vi.fn<(input: string, init?: RequestInit) => Promise<Response>>()
  for (const r of replies) fetch.mockResolvedValueOnce(r)
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('getJSON', () => {
  it('gives the reply', async () => {
    fakeFetch(reply(200, { poll_sec: 1 }))
    await expect(getJSON('/api/dev/settings')).resolves.toEqual({ poll_sec: 1 })
  })

  it('throws ApiError with the status and the server\'s own words', async () => {
    fakeFetch(reply(400, { error: 'nope' }))
    const err = await getJSON('/api/dev/files').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 400, message: 'nope' })
  })

  it('throws ApiError with status 0 when the server can\'t be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const err = await getJSON('/api/dev/settings').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 0 })
  })

  it('names the status when the reply has no error words', async () => {
    fakeFetch(new Response('<html>oops</html>', { status: 500, statusText: 'Internal Server Error' }))
    const err = await getJSON('/api/dev/settings').catch((e: unknown) => e)
    expect(err).toMatchObject({ status: 500, message: expect.stringContaining('500') })
  })
})

describe('postJSON', () => {
  it('sends the body as JSON', async () => {
    const fetch = fakeFetch(reply(200, { run_id: 'r1' }))
    await expect(postJSON('/api/dev/runs', { check: true })).resolves.toEqual({ run_id: 'r1' })
    const [path, init] = fetch.mock.calls[0]
    expect(path).toBe('/api/dev/runs')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ check: true })
  })

  it('throws ApiError on a 409', async () => {
    fakeFetch(reply(409, { error: 'A run is already going.' }))
    await expect(startTests(['tests/unit/test_a.py::test_x'])).rejects.toMatchObject({
      status: 409, message: 'A run is already going.',
    })
  })
})

describe('the route functions', () => {
  it('getTrace URL-encodes the label', async () => {
    const fetch = fakeFetch(reply(200, { label: 'pen/pencil', steps: [] }))
    await expect(getTrace('pen/pencil')).resolves.toEqual([])
    expect(fetch.mock.calls[0][0]).toBe('/api/dev/smoothing/trace?label=pen%2Fpencil')
  })
})
