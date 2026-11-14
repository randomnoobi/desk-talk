// One time chart of the Pi's numbers, drawn with uPlot: a line per thing (each core, each sensor ...) in one unit,
// and dashed level lines (the hot lines, the fastest a core can go). Hover shows every line's value at that moment
// in the legend under it. Gaps in a line are samples that didn't have it.
import { useEffect, useRef, useState } from 'react'
import type uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { Level } from './ui'

export interface ChartLine {
  label: string
  values: (number | null)[]        // one per time; null = not in that sample
}

export interface LevelLine {
  label: string
  value: number
  level: Level                     // warn = amber, bad = red, anything else grey
}

// How the chart is drawn. These are looks, not settings.
const HEIGHT_PX = 200
const LINE_PX = 2
// The line colours, in a fixed order (they're --series-1 .. --series-8 in style.css). A line's colour comes from
// its place in `lines`, so the tab gives its lines in a fixed order.
export const MOST_LINES = 8

function colours() {
  const css = getComputedStyle(document.documentElement)
  const get = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback
  const fallbacks = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
  return {
    series: fallbacks.map((f, i) => get(`--series-${i + 1}`, f)),
    ink3: get('--ink-3', '#8D88A0'), line: get('--line', '#E4E5EE'),
    mustard: get('--mustard', '#9A6C00'), danger: get('--danger', '#C8324A'),
  }
}

type Props = {
  label: string                    // what it shows, for screen readers
  times: number[]                  // seconds since 1970, oldest first
  lines: ChartLine[]
  levels?: LevelLine[]
  format: (v: number) => string    // a value in words with its unit, for the side and the legend
  range?: [number, number]         // a fixed up-down scale, e.g. [0, 100] for percents
  bytes?: boolean                  // the values are bytes: step the side in 1024s, so it reads 100 KB, 200 KB ...
}

// Side steps for bytes: 1, 2, 5, 10, 20, 50 ... of B, KB, MB, GB, TB.
const BYTE_STEPS = [0, 1, 2, 3, 4].flatMap(k => [1, 2, 5, 10, 20, 50, 100, 200, 500].map(m => m * 1024 ** k))

/** The side axis as wide as its longest label (uPlot's own autosize recipe), so "2000 MHz" isn't cut off. */
function fitSide(u: uPlot, values: string[] | null, axisIdx: number, cycleNum: number): number {
  const axis = u.axes[axisIdx] as uPlot.Axis & { _size?: number }
  if (cycleNum > 1 && axis._size) return axis._size
  const gap = (axis.ticks?.size ?? 10) + (axis.gap ?? 5) + 8
  const longest = (values ?? []).reduce((a, v) => (v.length > a.length ? v : a), '')
  if (!longest) return gap
  u.ctx.font = Array.isArray(axis.font) ? String(axis.font[0]) : String(axis.font ?? u.ctx.font)
  return Math.ceil(gap + u.ctx.measureText(longest).width / devicePixelRatio)
}

function chartData(p: Props, lines: ChartLine[]): uPlot.AlignedData {
  return [p.times, ...lines.map(l => l.values), ...(p.levels ?? []).map(lv => p.times.map(() => lv.value))]
}

export default function StatsChart(props: Props): JSX.Element {
  const box = useRef<HTMLElement>(null)
  const plot = useRef<uPlot | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const lines = props.lines.slice(0, MOST_LINES)
  const latest = useRef({ props, lines })
  latest.current = { props, lines }
  // The chart is made again only when its lines change; new samples just go in.
  const shape = JSON.stringify([lines.map(l => l.label), (props.levels ?? []).map(l => [l.label, l.level]),
                                props.range ?? null])

  useEffect(() => {
    const el = box.current
    if (!el) return
    let gone = false
    let resize: ResizeObserver | null = null
    // uPlot is loaded the first time a chart is drawn: it reads the screen as soon as it loads,
    // which the page's tests (with no real screen) can't do.
    import('uplot').then(({ default: UPlot }) => {
      if (gone) return
      const c = colours()
      const { props: p, lines: ls } = latest.current
      const show = (_u: uPlot, v: number | null | undefined) => (v === null || v === undefined ? '–' : p.format(v))
      const axis: uPlot.Axis = {
        stroke: c.ink3, grid: { stroke: c.line, width: 1 }, ticks: { stroke: c.line, width: 1 },
      }
      const opts: uPlot.Options = {
        width: Math.max(el.clientWidth, 1),
        height: HEIGHT_PX,
        cursor: { y: false },
        scales: { x: { time: true }, y: p.range ? { range: p.range } : {} },
        series: [
          {},
          ...ls.map((l, i): uPlot.Series => ({
            label: l.label, stroke: c.series[i % c.series.length], width: LINE_PX, spanGaps: false,
            points: { show: false }, value: show,
          })),
          ...(p.levels ?? []).map((lv): uPlot.Series => ({
            label: lv.label, stroke: lv.level === 'bad' ? c.danger : lv.level === 'warn' ? c.mustard : c.ink3,
            width: 1.5, dash: [6, 4], points: { show: false }, value: show,
          })),
        ],
        axes: [axis, { ...axis, size: fitSide, values: (_u, splits) => splits.map(v => p.format(v)),
                         ...(p.bytes ? { incrs: BYTE_STEPS } : {}) }],
      }
      const u = new UPlot(opts, chartData(p, ls), el)
      plot.current = u
      resize = new ResizeObserver(() => u.setSize({ width: Math.max(el.clientWidth, 1), height: HEIGHT_PX }))
      resize.observe(el)
    }).catch((e: unknown) => {
      if (!gone) setProblem(`Couldn't draw the chart: ${e instanceof Error ? e.message : String(e)}`)
    })
    return () => {
      gone = true
      resize?.disconnect()
      plot.current?.destroy()
      plot.current = null
    }
  }, [shape])

  useEffect(() => {
    plot.current?.setData(chartData(props, lines))
  })

  return (
    <>
      {problem && <p className="error" role="alert">{problem}</p>}
      <figure ref={box} className="stats-chart" aria-label={props.label} />
      {props.lines.length > MOST_LINES && (
        <p className="muted small">Showing the first {MOST_LINES} of {props.lines.length} lines.</p>
      )}
    </>
  )
}
