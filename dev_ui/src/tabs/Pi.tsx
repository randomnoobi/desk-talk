import { useEffect, useRef, useState, type ReactNode } from 'react'
import { getPiStats, type DevSettings, type PiSample, type PiStatsReply } from '../api'
import { formatBytes, formatClock, formatSeconds } from '../format'
import { formatRate, hottest, keepCount, lineNames, mergeSamples, series, tempLevel } from '../piStats'
import StatsChart, { type ChartLine, type LevelLine } from '../StatsChart'
import { ErrorLine, Pill, Table, type Level } from '../ui'
import { usePoll } from '../usePoll'

// Numbers in words, with their unit, for the charts' sides and legends.
const one = (v: number) => Number(v.toFixed(1))
const pct = (v: number) => `${one(v)}%`
const mhz = (v: number) => `${Math.round(v)} MHz`
const load = (v: number) => String(Number(v.toFixed(2)))
const celsius = (v: number) => `${one(v)} °C`
const rpm = (v: number) => `${Math.round(v)} RPM`
const dbm = (v: number) => `${Math.round(v)} dBm`
const volts = (v: number) => `${Number(v.toFixed(3))} V`
const share = (part: number, whole: number) => (whole > 0 ? Math.round(part / whole * 100) : 0)
const PERCENT: [number, number] = [0, 100]

/** The Pi tab: every system number the Pi gives, now and over the last system_stats.keep_min minutes. */
export default function Pi(props: { settings: DevSettings }): JSX.Element {
  const { settings } = props
  // The samples so far, oldest first. Each ask is for the ones newer than the newest here.
  const kept = useRef<PiSample[]>([])
  // How often to ask: the Pi's every_sec once it has said, settings.poll_sec until then.
  const [everySec, setEverySec] = useState<number | null>(null)
  const poll = usePoll(async () => {
    const newest = kept.current.length ? kept.current[kept.current.length - 1].at : undefined
    const reply = await getPiStats(newest)
    kept.current = mergeSamples(kept.current, reply.samples, keepCount(reply))
    return { reply, samples: kept.current }
  }, (everySec ?? settings.poll_sec) * 1000)
  const reply = poll.data?.reply ?? null
  useEffect(() => {
    if (reply && reply.every_sec > 0) setEverySec(reply.every_sec)
  }, [reply])

  const samples = poll.data?.samples ?? []
  const now = samples.length ? samples[samples.length - 1] : null
  return (
    <div className="stack">
      <Header reply={reply} now={now} settings={settings} error={poll.error} askEverySec={everySec ?? settings.poll_sec} />
      {now && reply && (
        <>
          <Tiles now={now} cores={reply.info.cores} settings={settings} />
          <Charts samples={samples} cores={reply.info.cores} settings={settings} />
          <Tables now={now} cores={reply.info.cores} />
        </>
      )}
    </div>
  )
}

function Header(props: { reply: PiStatsReply | null, now: PiSample | null, settings: DevSettings,
                         error: string | null, askEverySec: number }): JSX.Element {
  const { reply, now, settings, error, askEverySec } = props
  const info = reply?.info ?? {}
  const what = [info.model, info.kernel && `kernel ${info.kernel}`, info.cores && `${info.cores} cores`,
                reply && `at ${reply.address}`].filter(Boolean).join(' · ')
  const warnings: [Level, string][] = []
  if (now?.under_voltage) warnings.push(['bad', 'The power supply is too weak right now'])
  const hot = now ? hottest(now) : null
  if (hot) {
    const level = tempLevel(hot.c, settings.health)
    if (level !== 'ok') warnings.push([level, `The processor is hot: ${celsius(hot.c)}`])
  }
  const flags = now?.throttled ? parseInt(now.throttled, 16) : NaN
  if (now && !Number.isNaN(flags) && flags !== 0) warnings.push(['bad', `The Pi has slowed itself down (${now.throttled})`])

  return (
    <div className="card">
      <h2>Pi</h2>
      {what && <p>{what}</p>}
      <p className="muted small">
        {now
          ? `Up for ${formatSeconds(now.uptime_sec)}. Last sample at ${formatClock(now.at)}, asking again every ${askEverySec} s.`
          : reply?.collecting
            ? `No samples yet: the Pi makes its first one a reading after it starts. Asking again every ${askEverySec} s.`
            : !reply && `Asking the Pi how it is doing…`}
      </p>
      {reply && !reply.collecting && (
        <p><Pill level="warn">The Pi isn't collecting its numbers (system_stats.enabled is off there)</Pill></p>
      )}
      {warnings.length > 0 && (
        <div className="row warnings">{warnings.map(([level, words]) => <Pill key={words} level={level}>{words}</Pill>)}</div>
      )}
      <ErrorLine error={error} />
    </div>
  )
}

function Tile(props: { name: string, value: string, level?: Level, children?: ReactNode }): JSX.Element {
  return (
    <li className={`tile ${props.level ?? ''}`.trim()}>
      <span className="tile-name">{props.name}</span>
      <strong className="tile-value">{props.value}</strong>
      <span className="muted small">{props.children}</span>
    </li>
  )
}

function Tiles(props: { now: PiSample, cores: number | undefined, settings: DevSettings }): JSX.Element {
  const { now, cores, settings } = props
  const { cpu, memory } = now
  const disk = now.disks[0]         // the first of settings → system_stats.disks: the data folder
  const hot = hottest(now)
  const fan = now.fans[0]
  const wifi = now.wifi[0]
  return (
    <ul className="tiles" aria-label="The Pi now">
      <Tile name="CPU" value={`${Math.round(cpu.busy_pct)}%`}>{cpu.running} running, {cpu.processes} processes</Tile>
      <Tile name="Memory used" value={`${share(memory.used_bytes, memory.total_bytes)}%`}>
        {formatBytes(memory.used_bytes)} of {formatBytes(memory.total_bytes)}
      </Tile>
      {disk
        ? <Tile name={`${disk.label} used`} value={`${share(disk.used_bytes, disk.total_bytes)}%`}>{formatBytes(disk.free_bytes)} free</Tile>
        : <Tile name="Disk used" value="–">not reported</Tile>}
      {hot
        ? <Tile name="Hottest" value={celsius(hot.c)} level={tempLevel(hot.c, settings.health)}>{hot.name}</Tile>
        : <Tile name="Hottest" value="–">no sensor</Tile>}
      {fan
        ? <Tile name="Fan" value={rpm(fan.rpm)}>{fan.name}, {Math.round(fan.pwm_pct)}% power</Tile>
        : <Tile name="Fan" value="–">no fan</Tile>}
      {wifi
        ? <Tile name="Wi-Fi signal" value={dbm(wifi.signal_dbm)}>{wifi.name}, quality {Math.round(wifi.quality)}</Tile>
        : <Tile name="Wi-Fi signal" value="–">no Wi-Fi</Tile>}
      <Tile name="Load (1 min)" value={cpu.load.length ? load(cpu.load[0]) : '–'}>
        {cpu.load.length >= 3 && `5 min ${load(cpu.load[1])}, 15 min ${load(cpu.load[2])}`}
        {cores ? `, ${cores} cores` : ''}
      </Tile>
    </ul>
  )
}

/** A chart in its own card, or a line saying the Pi doesn't report it (a Mac, no fan) when no line has a value. */
function ChartCard(props: { title: string, note?: string, times: number[], lines: ChartLine[], levels?: LevelLine[],
                            format: (v: number) => string, range?: [number, number], bytes?: boolean }): JSX.Element {
  const { title, note, ...chart } = props
  const lines = chart.lines.filter(l => l.values.some(v => v !== null))
  return (
    <div className="card">
      <h3>{title}</h3>
      {note && <p className="muted small">{note}</p>}
      {lines.length
        ? <StatsChart label={title} {...chart} lines={lines} />
        : <p className="muted">The Pi doesn't report this.</p>}
    </div>
  )
}

/** A line per name in one of the samples' lists, e.g. a line per temperature sensor. */
function perName<T extends { name: string }>(samples: PiSample[], list: (s: PiSample) => T[],
                                             pick: (item: T) => number, label = (name: string) => name): ChartLine[] {
  return lineNames(samples, list).map(name => ({
    label: label(name),
    values: series(samples, s => {
      const item = list(s)?.find(i => i.name === name)
      return item ? pick(item) : null
    }),
  }))
}

/** A line per core, numbered from 1. */
function perCore(samples: PiSample[], list: (s: PiSample) => number[]): ChartLine[] {
  const count = Math.max(0, ...samples.map(s => list(s)?.length ?? 0))
  return Array.from({ length: count }, (_, i) => ({ label: `core ${i + 1}`, values: series(samples, s => list(s)?.[i]) }))
}

function Charts(props: { samples: PiSample[], cores: number | undefined, settings: DevSettings }): JSX.Element {
  const { samples, cores, settings } = props
  const times = samples.map(s => s.at)
  const now = samples[samples.length - 1]
  const line = (label: string, pick: (s: PiSample) => number | null | undefined): ChartLine =>
    ({ label, values: series(samples, pick) })
  const { pi_cpu_warn_c: warn, pi_cpu_bad_c: bad } = settings.health
  const hasSwap = samples.some(s => s.memory.swap_total_bytes > 0)

  return (
    <div className="chart-grid">
      <ChartCard title="CPU use" times={times} format={pct} range={PERCENT} lines={[
        line('busy', s => s.cpu.busy_pct), line('user', s => s.cpu.user_pct),
        line('system', s => s.cpu.system_pct), line('I/O wait', s => s.cpu.iowait_pct),
      ]} />
      <ChartCard title="Each core" times={times} format={pct} range={PERCENT} lines={perCore(samples, s => s.cpu.cores_pct)} />
      <ChartCard title="CPU speed" times={times} format={mhz} lines={perCore(samples, s => s.cpu.freq_mhz)}
                 levels={now.cpu.max_freq_mhz > 0 ? [{ label: 'fastest', value: now.cpu.max_freq_mhz, level: 'off' }] : []} />
      <ChartCard title="Load" times={times} format={load}
                 note="How many programs want the processor at once, on average. At the cores line, every core is busy."
                 lines={[line('1 min', s => s.cpu.load[0]), line('5 min', s => s.cpu.load[1]), line('15 min', s => s.cpu.load[2])]}
                 levels={cores ? [{ label: 'cores', value: cores, level: 'off' }] : []} />
      <ChartCard title="Temperatures" times={times} format={celsius} lines={perName(samples, s => s.temps, t => t.c)}
                 levels={[{ label: 'amber from', value: warn, level: 'warn' }, { label: 'red from', value: bad, level: 'bad' }]} />
      <ChartCard title="Fan speed" times={times} format={rpm} lines={perName(samples, s => s.fans, f => f.rpm)} />
      <ChartCard title="Fan power" times={times} format={pct} range={PERCENT}
                 lines={perName(samples, s => s.fans, f => f.pwm_pct)} />
      <ChartCard title="Memory" times={times} format={formatBytes} bytes lines={[
        line('used', s => s.memory.used_bytes), line('cached', s => s.memory.cached_bytes),
        line('available', s => s.memory.available_bytes), ...(hasSwap ? [line('swap used', s => s.memory.swap_used_bytes)] : []),
      ]} />
      <ChartCard title="Disk read and write" times={times} format={formatRate} bytes lines={lineNames(samples, s => s.disk_io).flatMap(name => [
        line(`${name} read`, s => s.disk_io.find(d => d.name === name)?.read_bps),
        line(`${name} write`, s => s.disk_io.find(d => d.name === name)?.write_bps),
      ])} />
      <ChartCard title="Disk busy" times={times} format={pct} range={PERCENT} note="How much of the time the disk was working."
                 lines={perName(samples, s => s.disk_io, d => d.busy_pct)} />
      <ChartCard title="Network received" times={times} format={formatRate} bytes lines={perName(samples, s => s.network, n => n.rx_bps)} />
      <ChartCard title="Network sent" times={times} format={formatRate} bytes lines={perName(samples, s => s.network, n => n.tx_bps)} />
      <ChartCard title="Wi-Fi signal" times={times} format={dbm} note="Closer to 0 is stronger."
                 lines={perName(samples, s => s.wifi, w => w.signal_dbm)} />
      <ChartCard title="Voltages" times={times} format={volts} lines={perName(samples, s => s.voltages, v => v.v)} />
    </div>
  )
}

function Tables(props: { now: PiSample, cores: number | undefined }): JSX.Element {
  const { now, cores } = props
  const none = "The Pi doesn't report this."
  return (
    <>
      <div className="card">
        <h3>Disk space</h3>
        <Table rows={now.disks} rowKey={d => d.label} empty={none} columns={[
          { label: 'Disk', cell: d => d.label },
          { label: 'Folder', cell: d => <span className="mono">{d.path}</span> },
          { label: 'Used', cell: d => (
            <span className="used">
              <span className="bar" aria-hidden="true"><span style={{ width: `${share(d.used_bytes, d.total_bytes)}%` }} /></span>
              {share(d.used_bytes, d.total_bytes)}%
            </span>
          ) },
          { label: 'Used of all', numeric: true, cell: d => `${formatBytes(d.used_bytes)} of ${formatBytes(d.total_bytes)}` },
          { label: 'Free', numeric: true, cell: d => formatBytes(d.free_bytes) },
        ]} />
      </div>
      <div className="card">
        <h3>Busiest programs</h3>
        <p className="muted small">
          CPU is of one core, like <code>top</code>{cores ? `, so it can go up to ${cores * 100}%` : ''}. From the newest sample.
        </p>
        <Table rows={now.top_processes} rowKey={p => String(p.pid)} empty="None yet." columns={[
          { label: 'Name', cell: p => p.name },
          { label: 'Command', cell: p => <span className="mono small">{p.command}</span> },
          { label: 'CPU', numeric: true, cell: p => pct(p.cpu_pct) },
          { label: 'Memory', numeric: true, cell: p => formatBytes(p.memory_bytes) },
        ]} />
      </div>
      <div className="card">
        <h3>Cooling</h3>
        <Table rows={now.cooling} rowKey={c => c.name} empty={none} columns={[
          { label: 'What', cell: c => c.name },
          { label: 'Step', cell: c => `${c.state} of ${c.max_state}` },
        ]} />
      </div>
      <div className="card">
        <h3>Network errors and drops</h3>
        <p className="muted small">Counted since the Pi started.</p>
        <Table rows={now.network} rowKey={n => n.name} empty={none} columns={[
          { label: 'Connection', cell: n => n.name },
          { label: 'Errors received', numeric: true, cell: n => n.rx_errors },
          { label: 'Errors sent', numeric: true, cell: n => n.tx_errors },
          { label: 'Dropped received', numeric: true, cell: n => n.rx_drops },
          { label: 'Dropped sent', numeric: true, cell: n => n.tx_drops },
        ]} />
      </div>
    </>
  )
}
