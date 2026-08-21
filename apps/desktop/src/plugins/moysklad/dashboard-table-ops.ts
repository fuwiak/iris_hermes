export type SortDir = 'asc' | 'desc'

export type ChannelSortKey = 'label' | 'turnover' | 'revenue' | 'margin' | 'orders' | 'avg_check'

export type ChannelLike = {
  key: string
  label: string
  turnover?: Array<number | null>
  revenue?: Array<number | null>
  margin?: Array<number | null>
  orders?: Array<number | null>
  avg_check?: Array<number | null>
}

export function matchesQuery(haystack: string, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) {
    return true
  }
  return haystack.toLowerCase().includes(needle)
}

export function filterChannels<T extends ChannelLike>(channels: T[], query: string): T[] {
  const needle = query.trim().toLowerCase()
  if (!needle) {
    return channels
  }
  return channels.filter(
    ch => ch.label.toLowerCase().includes(needle) || ch.key.toLowerCase().includes(needle)
  )
}

export function sortChannels<T extends ChannelLike>(
  channels: T[],
  sortKey: ChannelSortKey,
  dir: SortDir,
  periodIndex: number
): T[] {
  const sign = dir === 'asc' ? 1 : -1
  return [...channels].sort((a, b) => {
    if (sortKey === 'label') {
      return a.label.localeCompare(b.label, 'ru') * sign
    }
    const av = Number((a[sortKey] || [])[periodIndex] || 0)
    const bv = Number((b[sortKey] || [])[periodIndex] || 0)
    if (av === bv) {
      return a.label.localeCompare(b.label, 'ru')
    }
    return (av - bv) * sign
  })
}

export type DayRowLike = {
  id: string
  label: string
  kind?: string
  channels?: Record<string, { orders?: number; turnover?: number } | undefined>
}

export function filterDayRows<T extends DayRowLike>(rows: T[], query: string, channelKeys: string[]): T[] {
  const needle = query.trim().toLowerCase()
  return rows.filter(r => {
    if (r.kind !== 'month' && !channelKeys.some(k => Number(r.channels?.[k]?.orders || 0) > 0)) {
      return false
    }
    if (!needle) {
      return true
    }
    if (r.label.toLowerCase().includes(needle) || r.id.toLowerCase().includes(needle)) {
      return true
    }
    return channelKeys.some(k => k.toLowerCase().includes(needle))
  })
}

export function dayRowTotal(row: DayRowLike, channelKeys: string[], metric: 'orders' | 'turnover'): number {
  return channelKeys.reduce((sum, k) => sum + Number(row.channels?.[k]?.[metric] || 0), 0)
}

export function sortDayRows<T extends DayRowLike>(
  rows: T[],
  sortKey: 'date' | 'orders' | 'turnover',
  dir: SortDir,
  channelKeys: string[]
): T[] {
  const sign = dir === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    if (sortKey === 'date') {
      return a.id.localeCompare(b.id) * sign
    }
    const av = dayRowTotal(a, channelKeys, sortKey)
    const bv = dayRowTotal(b, channelKeys, sortKey)
    return (av - bv) * sign
  })
}

export function nextSortDir(current: SortDir): SortDir {
  return current === 'desc' ? 'asc' : 'desc'
}
