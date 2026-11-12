import { act, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getPiStats, type DevSettings, type PiSample, type PiStatsReply } from '../api'
import type { ChartLine } from '../StatsChart'
import Pi from './Pi'

vi.mock('../api', async original => ({
  ...await original<typeof import('../api')>(),
  getPiStats: vi.fn(),
}))
// uPlot needs a real canvas, which jsdom doesn't have: the chart here only says what it was given.
vi.mock('../StatsChart', () => ({
  default: (props: { label: string, times: number[], lines: ChartLine[] }) => (
    <div data-testid={`chart: ${props.label}`} data-values={JSON.stringify(props.lines.map(l => l.values))}>
      {props.times.length} samples: {props.lines.map(l => l.label).join(', ')}
    </div>
  ),
}))

const settings: DevSettings = {
  poll_sec: 5, checks: [], health: { pi_disk_warn_mb: 1000, detect_warn_ms: 500 }, pi_hot: { warning_c: 70, serious_c: 80 },
  trace_pictures: 30, sql_max_rows: 500, data_folder: '/data',
  disconnect_after_sec: 3, web_port: 5000, flicker_flips_per_min: 4,
  output_keep_kb: 2000, flips_window_min: 10, table_counts_every_sec: 30, trace_events_kept: 50,
  trace_photos_kept: 6, trace_step_photos_kept: 24,
  object_states: { on_confidence: 0.5, off_confidence: 0.3, appear_sec: 2, gone_sec: 5, window_pictures: 5, vote_share: 0.6 },
  scenarios: { countdown_default_sec: 5, countdown_max_sec: 600, waiting_max: 10 },
}

const GB = 1024 ** 3

function sample(at: number, over: Partial<PiSample> = {}): PiSample {
  return {
    at, uptime_sec: 86400,
    cpu: { busy_pct: 53.1, user_pct: 40, system_pct: 10, iowait_pct: 1, other_pct: 2.1,
           cores_pct: [50, 60, 45, 57.4], freq_mhz: [2400, 2400, 1500, 2400], max_freq_mhz: 2400,
           load: [2.44, 2.57, 2.62], running: 3, processes: 481 },
    memory: { total_bytes: 8 * GB, used_bytes: 2 * GB, available_bytes: 6 * GB, cached_bytes: 3 * GB,
              buffers_bytes: 0, free_bytes: 1 * GB, swap_total_bytes: 0, swap_used_bytes: 0 },
    temps: [{ name: 'cpu-thermal', c: 55.1 }],
    fans: [{ name: 'pwmfan', rpm: 3416, pwm_pct: 29.4 }],
    cooling: [{ name: 'pwm-fan', state: 1, max_state: 4 }],
    voltages: [{ name: 'rp1_adc in1', v: 1.477 }],
    under_voltage: false, throttled: '',
    disks: [{ label: 'data folder', path: '/desk-talk/data', total_bytes: 100 * GB, used_bytes: 25 * GB,
              free_bytes: 75 * GB }],
    disk_io: [{ name: 'mmcblk0', read_bps: 0, write_bps: 2048, reads_per_sec: 0, writes_per_sec: 1, busy_pct: 3 }],
    network: [{ name: 'wlan0', rx_bps: 1000, tx_bps: 500, rx_errors: 0, tx_errors: 0, rx_drops: 7, tx_drops: 0 }],
    wifi: [{ name: 'wlan0', quality: 46, signal_dbm: -64 }],
    top_processes: [{ pid: 1234, name: 'python', command: 'python -m desktalk_detector', cpu_pct: 95, memory_bytes: GB }],
    ...over,
  }
}

function reply(samples: PiSample[], over: Partial<PiStatsReply> = {}): PiStatsReply {
  return {
    collecting: true, every_sec: 2, keep_min: 30,
    info: { model: 'Raspberry Pi 5 Model B Rev 1.1', kernel: '6.18.50+rpt-rpi-2712', cores: 4 },
    samples, address: 'desk-pi.local', ...over,
  }
}

async function wait(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}

const chart = (label: string) => screen.getByTestId(`chart: ${label}`)

afterEach(() => vi.resetAllMocks())

describe('Pi tab', () => {
  it('shows the model and the big numbers from a reply', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000)]))
    render(<Pi settings={settings} />)
    expect(await screen.findByText(/Raspberry Pi 5 Model B Rev 1\.1/)).toBeInTheDocument()
    expect(screen.getByText(/6\.18\.50\+rpt-rpi-2712/)).toBeInTheDocument()

    const tiles = screen.getByRole('list', { name: 'The Pi now' })
    const tile = (name: string) => within(tiles).getByText(name).closest('li') as HTMLElement
    expect(tile('CPU')).toHaveTextContent('53%')
    expect(tile('Memory used')).toHaveTextContent('25%')
    expect(tile('data folder used')).toHaveTextContent('25%')
    expect(tile('Hottest')).toHaveTextContent('55.1 °C')
    expect(tile('Fan')).toHaveTextContent('3416 RPM')
    expect(tile('Wi-Fi signal')).toHaveTextContent('-64 dBm')
    expect(tile('Load (1 min)')).toHaveTextContent('2.44')
  })

  it('draws every chart, and the tables', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000), sample(1002)]))
    render(<Pi settings={settings} />)
    await screen.findByText(/Raspberry Pi 5/)
    expect(chart('CPU use')).toHaveTextContent('2 samples: busy, user, system, I/O wait')
    expect(chart('Each core')).toHaveTextContent('core 1, core 2, core 3, core 4')
    expect(chart('Temperatures')).toHaveTextContent('cpu-thermal')
    expect(chart('Network received')).toHaveTextContent('wlan0')
    for (const name of ['CPU speed', 'Load', 'Fan speed', 'Fan power', 'Memory', 'Disk read and write', 'Disk busy',
                        'Network sent', 'Wi-Fi signal', 'Voltages']) {
      expect(chart(name)).toBeInTheDocument()
    }
    expect(screen.getByText('python -m desktalk_detector')).toBeInTheDocument()
    expect(screen.getByText('/desk-talk/data')).toBeInTheDocument()
    expect(screen.getByText('1 of 4')).toBeInTheDocument()
  })

  it('says the Pi does not report a number instead of an empty chart (a Mac, no fan)', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { fans: [], wifi: [] })]))
    render(<Pi settings={settings} />)
    await screen.findByText(/Raspberry Pi 5/)
    expect(screen.queryByTestId('chart: Fan speed')).not.toBeInTheDocument()
    const fanCard = screen.getByRole('heading', { name: 'Fan speed' }).closest('.card') as HTMLElement
    expect(fanCard).toHaveTextContent("The Pi doesn't report this.")
  })

  it('warns when the power supply is too weak', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { under_voltage: true })]))
    render(<Pi settings={settings} />)
    expect(await screen.findByText('The power supply is too weak right now')).toBeInTheDocument()
  })

  it('leaves out what the Pi could not read instead of showing 0 (no fan power, no uptime, no process counts)', async () => {
    const cpu = sample(0).cpu
    vi.mocked(getPiStats).mockResolvedValue(reply([
      sample(1000),
      sample(1002, { uptime_sec: null, cpu: { ...cpu, running: null, processes: null, max_freq_mhz: null },
                     fans: [{ name: 'pwmfan', rpm: 3000, pwm_pct: null }] }),
    ]))
    render(<Pi settings={settings} />)
    await screen.findByText(/Raspberry Pi 5/)
    const tiles = screen.getByRole('list', { name: 'The Pi now' })
    const tile = (name: string) => within(tiles).getByText(name).closest('li') as HTMLElement
    expect(tile('Fan')).toHaveTextContent('3000 RPM')
    expect(tile('Fan')).toHaveTextContent('pwmfan')
    expect(tile('Fan')).not.toHaveTextContent('power')
    expect(tile('CPU')).toHaveTextContent('53%')
    expect(tile('CPU')).not.toHaveTextContent('running')
    expect(tile('CPU')).not.toHaveTextContent('processes')
    expect(screen.getByText(/Last sample at/)).not.toHaveTextContent('Up for')
    // The fan power chart has a gap where the Pi couldn't read it, not a 0.
    expect(JSON.parse(chart('Fan power').dataset.values ?? '')).toEqual([[29.4, null]])
  })

  it('shows red pills for slowing down now and one amber pill for earlier since the Pi started', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { under_voltage: true, throttled: '0x50005' })]))
    render(<Pi settings={settings} />)
    const power = await screen.findAllByText('The power supply is too weak right now')
    expect(power).toHaveLength(1)
    expect(screen.getByText('The processor is slowed down right now')).toHaveClass('bad')
    expect(screen.getByText('Earlier since the Pi started: the power supply was too weak, the processor was slowed down'))
      .toHaveClass('warn')
    expect(screen.queryByText(/slowed itself down/)).not.toBeInTheDocument()
  })

  it('shows only the amber pill when the Pi slowed down earlier but is fine now', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { throttled: '0x80000' })]))
    render(<Pi settings={settings} />)
    expect(await screen.findByText('Earlier since the Pi started: the processor was close to too hot')).toHaveClass('warn')
    expect(screen.queryByText(/right now/)).not.toBeInTheDocument()
  })

  it('shows no slow-down pill for 0x0 or when the Pi could not read the flags', async () => {
    for (const throttled of ['0x0', '']) {
      vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { throttled })]))
      const { unmount } = render(<Pi settings={settings} />)
      await screen.findByText(/Raspberry Pi 5/)
      expect(screen.queryByText(/right now|Earlier|slowed/)).not.toBeInTheDocument()
      unmount()
    }
  })

  it('warns amber when the processor is hot and red when it is very hot', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([sample(1000, { temps: [{ name: 'cpu-thermal', c: 82 }] })]))
    render(<Pi settings={settings} />)
    const warning = await screen.findByText('The processor is hot: 82 °C')
    expect(warning).toHaveClass('bad')
  })

  it('says when the Pi is not collecting its numbers', async () => {
    vi.mocked(getPiStats).mockResolvedValue(reply([], { collecting: false, info: {} }))
    render(<Pi settings={settings} />)
    expect(await screen.findByText(/The Pi isn't collecting its numbers/)).toBeInTheDocument()
  })

  it('shows the words of a 502 and keeps the charts it had', async () => {
    vi.useFakeTimers()
    vi.mocked(getPiStats)
      .mockResolvedValueOnce(reply([sample(1000), sample(1002)]))
      .mockRejectedValue(new ApiError(502, "Can't reach the Pi at desk-pi.local: timed out"))
    render(<Pi settings={settings} />)
    await wait(0)
    expect(chart('CPU use')).toHaveTextContent('2 samples')

    await wait(2000)
    expect(screen.getByRole('alert')).toHaveTextContent("Can't reach the Pi at desk-pi.local: timed out")
    expect(chart('CPU use')).toHaveTextContent('2 samples')
    expect(screen.getByText(/Raspberry Pi 5/)).toBeInTheDocument()
  })

  it('asks every every_sec for samples newer than its newest, and keeps at most keep_min of them', async () => {
    vi.useFakeTimers()
    // keep_min 0.1 at every_sec 2: Math.round(6 / 2) = 3 samples kept.
    const r = (samples: PiSample[]) => reply(samples, { every_sec: 2, keep_min: 0.1 })
    vi.mocked(getPiStats)
      .mockResolvedValueOnce(r([sample(1000), sample(1002)]))
      .mockResolvedValueOnce(r([sample(1004), sample(1006)]))
      .mockResolvedValue(r([]))
    render(<Pi settings={settings} />)
    // The first reply says every_sec is 2, not settings.poll_sec's 5: the tab asks again at once at the new pace.
    await wait(0)
    expect(getPiStats).toHaveBeenNthCalledWith(1, undefined)
    expect(getPiStats).toHaveBeenNthCalledWith(2, 1002)
    expect(chart('CPU use')).toHaveTextContent('3 samples')

    const asked = vi.mocked(getPiStats).mock.calls.length
    await wait(1999)
    expect(getPiStats).toHaveBeenCalledTimes(asked)
    await wait(1)
    expect(getPiStats).toHaveBeenCalledTimes(asked + 1)
    expect(getPiStats).toHaveBeenLastCalledWith(1006)
  })

  it('asks every settings.poll_sec until the first reply comes', async () => {
    vi.useFakeTimers()
    vi.mocked(getPiStats).mockRejectedValue(new ApiError(502, 'no Pi'))
    render(<Pi settings={settings} />)
    await wait(0)
    expect(getPiStats).toHaveBeenCalledTimes(1)
    await wait(settings.poll_sec * 1000 - 1)
    expect(getPiStats).toHaveBeenCalledTimes(1)
    await wait(1)
    expect(getPiStats).toHaveBeenCalledTimes(2)
  })
})
