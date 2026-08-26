import type { EChartsOption } from 'echarts'

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

export type ChartMetric = 'turnover' | 'revenue' | 'orders' | 'avg_check'

export type ChannelValues = {
  key: string
  label: string
  turnover?: Array<number | null>
  revenue?: Array<number | null>
  margin?: Array<number | null>
  orders?: Array<number | null>
  avg_check?: Array<number | null>
}

export function channelColor(key: string): string {
  return CHANNEL_COLORS[key] || CHANNEL_COLORS.other
}

export function seriesOf(ch: ChannelValues, metric: ChartMetric): number[] {
  const raw = ch[metric] || []
  return raw.map(v => Number(v) || 0)
}

export function visibleChannels<T extends { key: string }>(channels: T[], hidden: Set<string>): T[] {
  return channels.filter(ch => !hidden.has(ch.key))
}

const TEXT = '#1e2033'
const MUTED = '#f0daf5'
const GRID = '#592466'
const AXIS = '#7c3a8c'

function axisCommon() {
  return {
    axisLine: { lineStyle: { color: AXIS } },
    axisLabel: { color: MUTED },
    splitLine: { lineStyle: { color: GRID } }
  }
}

export function legendSelected(
  channels: { key: string; label: string }[],
  hidden: Set<string>
): Record<string, boolean> {
  return Object.fromEntries(channels.map(ch => [ch.label, !hidden.has(ch.key)]))
}

export function buildLineOption(
  periods: { id: string; label: string }[],
  channels: ChannelValues[],
  metric: ChartMetric,
  hidden: Set<string>
): EChartsOption {
  const shown = visibleChannels(channels, hidden)
  return {
    backgroundColor: 'transparent',
    textStyle: { color: TEXT },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#ffffff',
      borderColor: AXIS,
      textStyle: { color: TEXT }
    },
    legend: {
      type: 'scroll',
      textStyle: { color: MUTED },
      selected: legendSelected(channels, hidden)
    },
    grid: { left: 56, right: 16, top: 40, bottom: 56 },
    dataZoom: [
      { type: 'inside', filterMode: 'none' },
      { type: 'slider', height: 18, bottom: 8, borderColor: AXIS, fillerColor: 'rgba(216,180,254,0.18)' }
    ],
    xAxis: { type: 'category', data: periods.map(p => p.label), ...axisCommon() },
    yAxis: { type: 'value', ...axisCommon() },
    series: shown.map(ch => ({
      name: ch.label,
      type: 'line',
      smooth: true,
      showSymbol: periods.length <= 16,
      data: seriesOf(ch, metric),
      itemStyle: { color: channelColor(ch.key) },
      lineStyle: { width: 2.2 }
    }))
  }
}

export function buildStackOption(
  periods: { id: string; label: string }[],
  channels: ChannelValues[],
  metric: ChartMetric,
  hidden: Set<string>
): EChartsOption {
  const shown = visibleChannels(channels, hidden)
  return {
    backgroundColor: 'transparent',
    textStyle: { color: TEXT },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#ffffff',
      borderColor: AXIS,
      textStyle: { color: TEXT }
    },
    legend: {
      type: 'scroll',
      textStyle: { color: MUTED },
      selected: legendSelected(channels, hidden)
    },
    grid: { left: 56, right: 16, top: 40, bottom: 56 },
    dataZoom: [
      { type: 'inside', filterMode: 'none' },
      { type: 'slider', height: 18, bottom: 8, borderColor: AXIS, fillerColor: 'rgba(216,180,254,0.18)' }
    ],
    xAxis: { type: 'category', data: periods.map(p => p.label), ...axisCommon() },
    yAxis: { type: 'value', ...axisCommon() },
    series: shown.map(ch => ({
      name: ch.label,
      type: 'bar',
      stack: 'mix',
      data: seriesOf(ch, metric),
      itemStyle: { color: channelColor(ch.key) }
    }))
  }
}

export function buildPieOption(
  channels: ChannelValues[],
  metric: ChartMetric,
  hidden: Set<string>
): EChartsOption {
  const last = Math.max(0, (channels[0]?.[metric]?.length || 1) - 1)
  const shown = visibleChannels(channels, hidden)
    .map(ch => ({ name: ch.label, value: seriesOf(ch, metric)[last] || 0, key: ch.key }))
    .filter(d => d.value > 0)
  return {
    backgroundColor: 'transparent',
    textStyle: { color: TEXT },
    tooltip: {
      trigger: 'item',
      backgroundColor: '#ffffff',
      borderColor: AXIS,
      textStyle: { color: TEXT }
    },
    legend: { type: 'scroll', bottom: 0, textStyle: { color: MUTED } },
    series: [
      {
        type: 'pie',
        radius: ['38%', '68%'],
        data: shown.map(d => ({
          name: d.name,
          value: d.value,
          itemStyle: { color: channelColor(d.key) }
        }))
      }
    ]
  }
}

export function buildHeatmapTrace(
  periods: { id: string; label: string }[],
  channels: ChannelValues[],
  metric: ChartMetric,
  hidden: Set<string>
): { x: string[]; y: string[]; z: number[][] } {
  const shown = visibleChannels(channels, hidden)
  return {
    x: periods.map(p => p.label),
    y: shown.map(ch => ch.label),
    z: shown.map(ch => seriesOf(ch, metric))
  }
}

export function buildHeatmapOption(
  periods: { id: string; label: string }[],
  channels: ChannelValues[],
  metric: ChartMetric,
  hidden: Set<string>
): EChartsOption {
  const heat = buildHeatmapTrace(periods, channels, metric, hidden)
  const data: Array<[number, number, number]> = []
  let max = 0
  heat.z.forEach((row, yi) => {
    row.forEach((value, xi) => {
      data.push([xi, yi, value])
      if (value > max) {
        max = value
      }
    })
  })
  return {
    backgroundColor: 'transparent',
    textStyle: { color: TEXT },
    tooltip: {
      position: 'top',
      backgroundColor: '#ffffff',
      borderColor: AXIS,
      textStyle: { color: TEXT }
    },
    grid: { left: 110, right: 24, top: 16, bottom: 48 },
    xAxis: { type: 'category', data: heat.x, splitArea: { show: true }, ...axisCommon() },
    yAxis: { type: 'category', data: heat.y, splitArea: { show: true }, ...axisCommon() },
    visualMap: {
      min: 0,
      max: max || 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: { color: MUTED },
      inRange: { color: ['#fcfcfe', '#8b5cf6', '#7137f5'] }
    },
    series: [{ type: 'heatmap', data, emphasis: { itemStyle: { shadowBlur: 8 } } }]
  }
}
