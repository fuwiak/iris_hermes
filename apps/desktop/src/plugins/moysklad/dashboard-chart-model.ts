export const CHANNEL_COLORS: Record<string, string> = {
  yandex_market: '#e8b86d',
  flavy: '#e39ac4',
  yandex_eda: '#e89b6c',
  ozon: '#8fb0e4',
  flowwow: '#8fd0b8',
  floday: '#b4c98a',
  skyloft: '#c4b4ea',
  direct: '#d8b4fe',
  other: '#94a3b8'
}

export const CHART_PAD = { l: 52, r: 16, t: 16, b: 40 }

export type ChartMetric = 'turnover' | 'revenue' | 'orders' | 'avg_check'

export function channelColor(key: string): string {
  return CHANNEL_COLORS[key] || CHANNEL_COLORS.other
}

export function nearestPeriodIndex(
  x: number,
  width: number,
  count: number,
  padL = CHART_PAD.l,
  padR = CHART_PAD.r
): number {
  if (count <= 1) {
    return 0
  }
  const inner = Math.max(1, width - padL - padR)
  const t = (x - padL) / inner
  return Math.max(0, Math.min(count - 1, Math.round(t * (count - 1))))
}

export function xAt(
  i: number,
  count: number,
  width: number,
  padL = CHART_PAD.l,
  padR = CHART_PAD.r
): number {
  if (count <= 1) {
    return padL + (width - padL - padR) / 2
  }
  return padL + (i / (count - 1)) * (width - padL - padR)
}

export function yAt(
  value: number,
  max: number,
  height: number,
  padT = CHART_PAD.t,
  padB = CHART_PAD.b
): number {
  const inner = Math.max(1, height - padT - padB)
  const safeMax = max <= 0 ? 1 : max
  return padT + (1 - Math.max(0, value) / safeMax) * inner
}

export function linePoints(
  values: Array<number | null | undefined>,
  max: number,
  width: number,
  height: number
): string {
  return values
    .map((v, i) => `${xAt(i, values.length, width)},${yAt(Number(v) || 0, max, height)}`)
    .join(' ')
}

export function niceMax(raw: number): number {
  if (raw <= 0) {
    return 1
  }
  const exp = 10 ** Math.floor(Math.log10(raw))
  const n = raw / exp
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10
  return nice * exp
}

export type StackSeg = { key: string; y0: number; y1: number; value: number }

export function stackColumns(
  series: { key: string; values: number[] }[],
  periodCount: number
): StackSeg[][] {
  const cols: StackSeg[][] = []
  for (let i = 0; i < periodCount; i++) {
    let y0 = 0
    const segs: StackSeg[] = []
    for (const s of series) {
      const value = Number(s.values[i] || 0)
      if (value <= 0) {
        continue
      }
      const y1 = y0 + value
      segs.push({ key: s.key, y0, y1, value })
      y0 = y1
    }
    cols.push(segs)
  }
  return cols
}
