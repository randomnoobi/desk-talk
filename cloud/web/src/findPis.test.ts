// The page's own search of the home network (findPis in api.ts): it asks the server where to look, then knocks
// on each address from the browser. Every address is a fake: nothing here touches a real network.
import { describe, expect, it } from 'vitest'
import { ApiError, findPis } from './api'
import { fakeServer, helloAt, homeNetwork, WHERE_TO_LOOK, type Reply } from './fakeServer'

const DESK = { address: '192.168.86.10', name: 'desktalk', linked: false }
const KITCHEN = { address: '192.168.86.9', name: 'kitchen', linked: true }
const JUNK = '192.168.86.50'
const looking = (addresses: string[], where: Partial<typeof WHERE_TO_LOOK> = {}) => ({ ...where, addresses })

describe('findPis', () => {
  it('asks the server where to look, then gives the Pis that answer, sorted by address as a number', async () => {
    fakeServer(homeNetwork([DESK, KITCHEN], looking([DESK.address, JUNK, KITCHEN.address])))
    expect(await findPis()).toEqual([KITCHEN, DESK])          // .9 before .10
  })

  it('knocks on each address it was given, on the port it was given, and nowhere else', async () => {
    const server = fakeServer(homeNetwork([DESK], looking([DESK.address, JUNK], { port: 5123 })))
    await findPis()
    const knocks = server.calls.filter(c => !c.path.startsWith('/'))
    expect(knocks.map(c => c.path).sort()).toEqual([helloAt(DESK.address, 5123), helloAt(JUNK, 5123)].sort())
    expect(server.toServer().map(c => `${c.method} ${c.path}`)).toEqual(['GET /api/pis/where_to_look'])
  })

  it('knocks without cookies, follows no redirect, keeps no copy, and can be stopped', async () => {
    const server = fakeServer(homeNetwork([DESK], looking([DESK.address])))
    await findPis()
    const [knock] = server.made('GET', helloAt(DESK.address))
    expect(knock.init).toMatchObject({ mode: 'cors', credentials: 'omit', redirect: 'error', cache: 'no-store' })
    expect(knock.init?.signal).toBeInstanceOf(AbortSignal)
  })

  it.each<[string, Reply]>([
    ['a router page', { text: '<html><body>Router admin</body></html>' }],
    ['something else', { body: { desk_talk: 'router', name: 'r', linked: false } }],
    ['no kind', { body: { name: 'no-kind', linked: false } }],
    ['linked as text', { body: { desk_talk: 'pi', name: 'r', linked: 'yes' } }],
    ['linked as a number', { body: { desk_talk: 'pi', name: 'r', linked: 1 } }],
    ['a bell in the name', { body: { desk_talk: 'pi', name: 'bad\u0007name', linked: false } }],
    ['two lines', { body: { desk_talk: 'pi', name: 'bad\nname', linked: false } }],
    ['half a letter', { text: '{"desk_talk": "pi", "name": "\\ud800", "linked": false}' }],
    ['a blank name', { body: { desk_talk: 'pi', name: '   ', linked: false } }],
    ['a number for a name', { body: { desk_talk: 'pi', name: 5, linked: false } }],
    ['a list', { body: ['desk_talk', 'pi'] }],
    ['an error status', { status: 500, body: { desk_talk: 'pi', name: 'r', linked: false } }],
    ['another good status', { status: 201, body: { desk_talk: 'pi', name: 'r', linked: false } }],
    ['too big', { text: `{"desk_talk": "pi", "name": "${'x'.repeat(5000)}", "linked": false}` }],
    ['nothing', { text: '' }],
  ])('leaves out an address that answers with %s', async (_what, reply) => {
    fakeServer({ ...homeNetwork([DESK], looking([JUNK, DESK.address])), [`GET ${helloAt(JUNK)}`]: reply })
    expect(await findPis()).toEqual([DESK])
  })

  it('leaves out an address whose knock fails, as one with no CORS header or a redirect does', async () => {
    fakeServer({
      ...homeNetwork([DESK], looking([JUNK, DESK.address])),
      [`GET ${helloAt(JUNK)}`]: () => { throw new TypeError('Failed to fetch') },
    })
    expect(await findPis()).toEqual([DESK])
  })

  it('gives up on an address that never answers after wait_ms', async () => {
    fakeServer({
      ...homeNetwork([DESK], looking([JUNK, DESK.address], { wait_ms: 30 })),
      [`GET ${helloAt(JUNK)}`]: () => new Promise<Reply>(() => {}),
    })
    expect(await findPis()).toEqual([DESK])
  })

  it('cuts a long name to name_max_chars, letter by letter', async () => {
    const long = { address: DESK.address, name: `  ${'😀'.repeat(10)}  `, linked: false }
    fakeServer(homeNetwork([long], looking([DESK.address], { name_max_chars: 4 })))
    expect(await findPis()).toEqual([{ ...long, name: '😀😀😀😀' }])
  })

  it('knocks on at most at_once addresses at the same time', async () => {
    const addresses = Array.from({ length: 20 }, (_, n) => `192.168.86.${100 + n}`)
    const busy = { now: 0, most: 0 }
    const routes = homeNetwork([], looking(addresses, { at_once: 3 }))
    for (const address of addresses) {
      routes[`GET ${helloAt(address)}`] = async () => {
        busy.now += 1
        busy.most = Math.max(busy.most, busy.now)
        await new Promise(resolve => setTimeout(resolve, 5))
        busy.now -= 1
        return { status: 404 }
      }
    }
    fakeServer(routes)
    expect(await findPis()).toEqual([])
    expect(busy.most).toBe(3)
  })

  it('knocks only on addresses written as four numbers', async () => {
    const server = fakeServer(homeNetwork([DESK], looking(['evil.example.com', '192.168.86.10/x', DESK.address])))
    expect(await findPis()).toEqual([DESK])
    expect(server.calls.filter(c => !c.path.startsWith('/')).map(c => c.path)).toEqual([helloAt(DESK.address)])
  })

  it("throws the server's words when there is no home network to look on", async () => {
    fakeServer({ 'GET /api/pis/where_to_look': { status: 409, body: { detail: 'Set PUBLIC_URL first.' } } })
    await expect(findPis()).rejects.toEqual(new ApiError(409, 'Set PUBLIC_URL first.'))
  })
})
