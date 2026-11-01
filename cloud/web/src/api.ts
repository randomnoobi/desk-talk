// Every call the website makes to the server, and the shape of each reply. Nothing else in the page fetches,
// so if a route's reply looks different from what is written here, this is the one file to fix.
//
// Routes used (the Pi's /v1 routes also answer to the person's login cookie):
//   GET  /auth/google/login                     POST /auth/logout        POST /auth/admin/login
//   GET  /api/me                                DELETE /api/me
//   GET  /api/devices                           POST /api/devices/code   PATCH|DELETE /api/devices/{id}
//   DELETE /api/devices/{id}/data
//   GET  /v1/devices/{id}/state                 GET  /v1/devices/{id}/events?since=&after_id=
//   GET  /api/reports?device_id=                GET  /api/reports/{id}
//   GET  /api/admin/overview                    POST /api/admin/reports/{id}/rerun

/** A call that didn't work. `status` is the HTTP status, or 0 when the server couldn't be reached. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The words for a call that never got an answer (status 0). */
export const NO_ANSWER = "Can't reach the server."

type Fields = Record<string, unknown>

// The server says what went wrong as {"detail": "..."} (FastAPI) or {"error": "..."}.
function errorWords(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const { error, detail } = body as { error?: unknown, detail?: unknown }
  if (typeof detail === 'string' && detail) return detail
  if (typeof error === 'string' && error) return error
  return null
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return null
  }
}

async function send(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError(0, NO_ANSWER)
  }
  const body = await readBody(response)
  if (!response.ok) {
    throw new ApiError(response.status, errorWords(body) ?? `${response.status} ${response.statusText}`.trim())
  }
  return body
}

const get = (path: string) => send(path)
const post = (path: string, body?: unknown) =>
  send(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })
const patch = (path: string, body: unknown) =>
  send(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const del = (path: string) => send(path, { method: 'DELETE' })

function fields(value: unknown): Fields {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Fields : {}
}

// A list may come bare or inside an object ({"devices": [...]}): take it either way.
function listOf(body: unknown, key: string): Fields[] {
  const list = Array.isArray(body) ? body : fields(body)[key]
  return Array.isArray(list) ? list.map(fields) : []
}

const text = (value: unknown, otherwise = '') => typeof value === 'string' ? value : otherwise
const num = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null

// ---------------------------------------------------------------- logging in

/** Asks the server whether Google login is ready. Null means yes (it answered with a redirect to Google);
 *  otherwise the words to show: the server's own setup message, or that it couldn't be reached. */
export async function googleLoginProblem(path: string): Promise<string | null> {
  let response: Response
  try {
    response = await fetch(path, { redirect: 'manual' })
  } catch {
    return NO_ANSWER
  }
  if (response.type === 'opaqueredirect' || response.ok || (response.status >= 300 && response.status < 400)) return null
  return errorWords(await readBody(response)) ?? `${response.status} ${response.statusText}`.trim()
}

export interface Me { email: string, name: string }

export async function getMe(): Promise<Me> {
  const body = fields(await get('/api/me'))
  return { email: text(body.email), name: text(body.name) }
}

export async function logout(): Promise<void> {
  await post('/auth/logout')
}

export async function deleteMe(): Promise<void> {
  await del('/api/me')
}

// ---------------------------------------------------------------- Pis

export interface Device {
  id: string
  name: string
  time_zone: string
  last_seen_at: number | null
  removed: boolean
}

function toDevice(raw: Fields): Device {
  return {
    id: text(raw.id),
    name: text(raw.name),
    time_zone: text(raw.time_zone),
    last_seen_at: num(raw.last_seen_at),
    removed: raw.removed === true || num(raw.removed_at) !== null,
  }
}

export async function listDevices(): Promise<Device[]> {
  return listOf(await get('/api/devices'), 'devices').map(toDevice)
}

export interface CodeReply {
  code: string
  expires_at: number | null
  server_url: string
  command: string
}

/** Makes a one-time code. With `deviceId` it is a code to link that same (kept) Pi again. */
export async function makeCode(timeZone: string, deviceId?: string): Promise<CodeReply> {
  const body = fields(await post('/api/devices/code', deviceId ? { time_zone: timeZone, device_id: deviceId } : { time_zone: timeZone }))
  return { code: text(body.code), expires_at: num(body.expires_at), server_url: text(body.server_url), command: text(body.command) }
}

export async function renameDevice(id: string, name: string): Promise<void> {
  await patch(`/api/devices/${encodeURIComponent(id)}`, { name })
}

/** Removes the Pi: its token stops working, its history stays. */
export async function removeDevice(id: string): Promise<void> {
  await del(`/api/devices/${encodeURIComponent(id)}`)
}

/** Deletes the Pi and everything stored for it. */
export async function deleteDeviceWithData(id: string): Promise<void> {
  await del(`/api/devices/${encodeURIComponent(id)}/data`)
}

// ---------------------------------------------------------------- one Pi

export interface ObjectState { label: string, state: string, since: number | null }

export interface DeviceState {
  device: { id: string, name: string, time_zone: string, removed: boolean }
  last_seen_at: number | null
  goals: Fields | null
  last_camera: Fields | null
  objects: ObjectState[]
}

export async function getState(deviceId: string): Promise<DeviceState> {
  const body = fields(await get(`/v1/devices/${encodeURIComponent(deviceId)}/state`))
  const device = fields(body.device)
  return {
    device: { id: text(device.id, deviceId), name: text(device.name), time_zone: text(device.time_zone), removed: device.removed === true },
    last_seen_at: num(body.last_seen_at),
    goals: body.goals ? fields(body.goals) : null,
    last_camera: body.last_camera ? fields(body.last_camera) : null,
    objects: listOf(body, 'objects').map(o => ({ label: text(o.label), state: text(o.state), since: num(o.since) })),
  }
}

export interface CloudEvent { id: string, ts: number, type: string, details: Fields }

interface EventsPage { events: CloudEvent[], next_since: number, next_after_id: string | null }

async function getEventsPage(deviceId: string, since: number, afterId: string | null): Promise<EventsPage> {
  const query = new URLSearchParams({ since: String(since) })
  if (afterId !== null) query.set('after_id', afterId)
  const body = fields(await get(`/v1/devices/${encodeURIComponent(deviceId)}/events?${query}`))
  const events = listOf(body, 'events').map(raw => {
    const { id, ts, type, ...details } = raw
    return { id: text(id), ts: num(ts) ?? 0, type: text(type), details }
  })
  return { events, next_since: num(body.next_since) ?? since, next_after_id: typeof body.next_after_id === 'string' ? body.next_after_id : null }
}

/** Every event after `since` (seconds since 1970), newest first. The server gives them oldest first, a page
 *  at a time, with the place to carry on from; this keeps asking until a page comes back empty or no
 *  further on than the last one. */
export async function recentEvents(deviceId: string, since: number): Promise<CloudEvent[]> {
  const found = new Map<string, CloudEvent>()
  let from = since
  let after: string | null = null
  for (;;) {
    const page = await getEventsPage(deviceId, from, after)
    for (const event of page.events) found.set(event.id, event)
    const samePlace = page.next_since === from && page.next_after_id === after
    if (page.events.length === 0 || samePlace) break
    from = page.next_since
    after = page.next_after_id
  }
  return [...found.values()].sort((a, b) => b.ts - a.ts)
}

// ---------------------------------------------------------------- reports

export interface Report {
  id: string
  device_id: string
  week_start: string
  status: string
  markdown: string | null
  error: string | null
}

function toReport(raw: Fields): Report {
  return {
    id: text(raw.id),
    device_id: text(raw.device_id),
    week_start: text(raw.week_start),
    status: text(raw.status),
    markdown: typeof raw.markdown === 'string' ? raw.markdown : null,
    error: typeof raw.error === 'string' && raw.error ? raw.error : null,
  }
}

export async function getReport(id: string): Promise<Report> {
  return toReport(fields(await get(`/api/reports/${encodeURIComponent(id)}`)))
}

export interface ReportRow { report: Report, deviceName: string }

/** The reports of every Pi the person has, newest week first. */
export async function listAllReports(): Promise<ReportRow[]> {
  const devices = await listDevices()
  const perDevice = await Promise.all(devices.map(async device => {
    const body = await get(`/api/reports?device_id=${encodeURIComponent(device.id)}`)
    return listOf(body, 'reports').map(raw => ({ report: toReport({ device_id: device.id, ...raw }), deviceName: device.name }))
  }))
  return perDevice.flat().sort((a, b) => b.report.week_start.localeCompare(a.report.week_start))
}

// ---------------------------------------------------------------- admin

export interface AdminUser { id: string, email: string, name: string }
export interface AdminDevice { id: string, name: string, owner_id: string, last_seen_at: number | null, removed: boolean }
export interface AdminFailure { id: string, device_id: string, week_start: string, error: string | null }
export interface AdminOverview { users: AdminUser[], devices: AdminDevice[], failed_reports: AdminFailure[] }

export async function getAdminOverview(): Promise<AdminOverview> {
  const body = await get('/api/admin/overview')
  return {
    users: listOf(body, 'users').map(u => ({ id: text(u.id), email: text(u.email), name: text(u.name) })),
    devices: listOf(body, 'devices').map(d => ({ ...toDevice(d), owner_id: text(d.user_id) })),
    failed_reports: listOf(body, 'failed_reports').map(r => ({
      id: text(r.id), device_id: text(r.device_id), week_start: text(r.week_start), error: typeof r.error === 'string' && r.error ? r.error : null,
    })),
  }
}

export async function adminLogin(username: string, password: string): Promise<void> {
  await post('/auth/admin/login', { username, password })
}

export async function rerunReport(id: string): Promise<void> {
  await post(`/api/admin/reports/${encodeURIComponent(id)}/rerun`)
}
