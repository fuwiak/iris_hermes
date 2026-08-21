import { describe, expect, test } from 'vitest'

import { formatPct } from './dashboard-analytics'
import {
  buildHeatmapOption,
  buildHeatmapTrace,
  buildLineOption,
  buildPieOption,
  buildStackOption,
  channelColor,
  legendSelected
} from './dashboard-chart-model'
import { filterChannels, sortChannels, sortDayRows } from './dashboard-table-ops'

describe('formatPct (Excel growth =(new/old)-1)', () => {
  test('renders signed percent', () => {
    expect(formatPct(0.81)).toBe('+81%')
    expect(formatPct(-0.25)).toBe('-25%')
    expect(formatPct(null)).toBe('')
    expect(formatPct(0)).toBe('0%')
  })
})

describe('chart option builders', () => {
  const periods = [
    { id: '2026-07', label: 'июль' },
    { id: '2026-08', label: 'август' }
  ]
  const channels = [
    { key: 'flowwow', label: 'Флау вау', turnover: [10, 80] },
    { key: 'direct', label: 'Прямые', turnover: [50, 20] }
  ]

  test('line series skip hidden channels', () => {
    const opt = buildLineOption(periods, channels, 'turnover', new Set(['direct']))
    const series = opt.series as { name: string; data: number[] }[]
    expect(series.map(s => s.name)).toEqual(['Флау вау'])
    expect(series[0].data).toEqual([10, 80])
    expect(legendSelected(channels, new Set(['direct'])).Прямые).toBe(false)
  })

  test('stack is bar+stack mix', () => {
    const opt = buildStackOption(periods, channels, 'turnover', new Set())
    const series = opt.series as { type: string; stack: string }[]
    expect(series.every(s => s.type === 'bar' && s.stack === 'mix')).toBe(true)
  })

  test('pie uses last period', () => {
    const opt = buildPieOption(channels, 'turnover', new Set())
    const pie = (opt.series as { data: { name: string; value: number }[] }[])[0]
    expect(pie.data).toEqual([
      { name: 'Флау вау', value: 80, itemStyle: { color: channelColor('flowwow') } },
      { name: 'Прямые', value: 20, itemStyle: { color: channelColor('direct') } }
    ])
  })

  test('heatmap z is channel × period', () => {
    const heat = buildHeatmapTrace(periods, channels, 'turnover', new Set())
    expect(heat.x).toEqual(['июль', 'август'])
    expect(heat.y).toEqual(['Флау вау', 'Прямые'])
    expect(heat.z).toEqual([
      [10, 80],
      [50, 20]
    ])
    const opt = buildHeatmapOption(periods, channels, 'turnover', new Set())
    const series = opt.series as { type: string; data: Array<[number, number, number]> }[]
    expect(series[0].type).toBe('heatmap')
    expect(series[0].data).toContainEqual([1, 0, 80])
  })
})

describe('dashboard table filter/sort', () => {
  const channels = [
    { key: 'flowwow', label: 'Флау вау', turnover: [10, 80], orders: [1, 4] },
    { key: 'direct', label: 'Прямые продажи', turnover: [50, 20], orders: [2, 1] }
  ]

  test('filter keeps matching channel label', () => {
    expect(filterChannels(channels, 'флау').map(c => c.key)).toEqual(['flowwow'])
  })

  test('sort by last-period turnover desc', () => {
    expect(sortChannels(channels, 'turnover', 'desc', 1).map(c => c.key)).toEqual(['flowwow', 'direct'])
    expect(sortChannels(channels, 'label', 'asc', 1)[0].key).toBe('direct')
  })

  test('sort day rows by turnover', () => {
    const rows = [
      { id: '2026-08-01', label: '1 авг', channels: { flowwow: { orders: 1, turnover: 100 } } },
      { id: '2026-08-02', label: '2 авг', channels: { flowwow: { orders: 2, turnover: 500 } } }
    ]
    expect(sortDayRows(rows, 'turnover', 'desc', ['flowwow'])[0].id).toBe('2026-08-02')
    expect(sortDayRows(rows, 'date', 'asc', ['flowwow'])[0].id).toBe('2026-08-01')
  })
})

