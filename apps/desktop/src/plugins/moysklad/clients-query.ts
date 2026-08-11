/**
 * Pure helpers for MoySklad clients / audience search.
 * Instant local filter from painted rows or localStorage cache;
 * network AbortError must not become a blocking error modal.
 */

export function isBenignRequestAbort(err: unknown): boolean {
  if (err == null) {
    return false
  }
  if (err instanceof DOMException && err.name === 'AbortError') {
    return true
  }
  if (err instanceof Error) {
    if (err.name === 'AbortError') {
      return true
    }
    return /abort/i.test(err.message)
  }
  return /abort/i.test(String(err))
}

/** Digits-only phone blob — mirrors plugins/moysklad/dedupe.normalize_phone loosely. */
export function digitsPhone(raw: string): string {
  const digits = String(raw || '').replace(/\D+/g, '')
  if (!digits) {
    return ''
  }
  if (digits.length === 11 && digits.startsWith('8')) {
    return `7${digits.slice(1)}`
  }
  return digits
}

export interface ClientQueryRow {
  id?: string | null
  name?: string | null
  phone?: string | null
  email?: string | null
  tg_nick?: string | null
  tags?: string[] | null
  groups?: string | null
  ms_groups?: string | null
  channel?: string | null
  channels?: string[] | null
  state?: string | null
  tg_conversation?: string | null
  tg_conversation_preview?: string | null
  actual_address?: string | null
}

/** Same spirit as classify._row_matches_query — name / phone / @tg / tags. */
export function rowMatchesClientQuery(row: ClientQueryRow, q: string): boolean {
  const needle = String(q || '')
    .trim()
    .toLowerCase()
  if (!needle) {
    return true
  }

  const channels = Array.isArray(row.channels)
    ? row.channels.map(String).join(' ')
    : String(row.channel || '')
  const blob = [
    row.name,
    row.phone,
    row.email,
    row.tg_nick,
    row.state,
    row.groups,
    row.ms_groups,
    row.tg_conversation,
    row.tg_conversation_preview,
    row.actual_address,
    channels,
    ...(Array.isArray(row.tags) ? row.tags : [])
  ]
    .map(x => String(x || '').toLowerCase())
    .join(' ')

  if (blob.includes(needle)) {
    return true
  }

  const needlePhone = digitsPhone(needle)
  if (!needlePhone) {
    return false
  }
  const rowPhone = digitsPhone(String(row.phone || ''))
  if (!rowPhone) {
    return false
  }
  return (
    needlePhone.includes(rowPhone) ||
    rowPhone.includes(needlePhone) ||
    rowPhone.endsWith(needlePhone) ||
    needlePhone.endsWith(rowPhone)
  )
}

export function filterClientRowsByQuery<T extends ClientQueryRow>(rows: T[], q: string): T[] {
  const needle = String(q || '').trim()
  if (!needle) {
    return rows
  }
  return rows.filter(row => rowMatchesClientQuery(row, needle))
}

/**
 * Prefer exact cache hit; else filter the empty-q snapshot for the same tabs
 * (and fall back to sales_filter=all — backend search also spans all tabs).
 */
export function pickLocalClientsSeed<T>(opts: {
  q: string
  readExact: () => T | null
  readBase: () => T | null
  readAllBase: () => T | null
  filterRows: (seed: T, q: string) => T | null
}): T | null {
  const exact = opts.readExact()
  if (exact) {
    return exact
  }
  const q = String(opts.q || '').trim()
  if (!q) {
    return opts.readBase()
  }
  for (const seed of [opts.readBase(), opts.readAllBase()]) {
    if (!seed) {
      continue
    }
    const filtered = opts.filterRows(seed, q)
    if (filtered) {
      return filtered
    }
  }
  return null
}
