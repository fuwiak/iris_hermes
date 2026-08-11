/**
 * Pure helpers for MoySklad clients / audience search + group chips.
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

/** Mirrors plugins/moysklad/groups.normalize_group_key (bouquet / spacing). */
export function normalizeGroupKey(name: string): string {
  let raw = String(name || '')
    .trim()
    .toLowerCase()
    .replace(/ё/g, 'е')
  raw = raw.replace(/\s+/g, ' ')
  if (!raw) {
    return ''
  }
  const compact = raw.replace(/[\s.\-_,]/g, '')
  if (compact.includes('букет') && compact.includes('10000')) {
    return 'букет от 10 000'
  }
  if (compact === 'watsapp' || compact === 'whatsapp') {
    return 'whatsapp'
  }
  if (compact === 'флаувау' || compact === 'флау вау'.replace(/\s+/g, '')) {
    return 'флау вау'
  }
  return raw
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
  ai_groups?: string[] | null
  channel?: string | null
  channels?: string[] | null
  state?: string | null
  tg_conversation?: string | null
  tg_conversation_preview?: string | null
  actual_address?: string | null
}

function splitGroupTokens(raw: string): string[] {
  return String(raw || '')
    .split(/[,;|]/)
    .map(s => s.trim())
    .filter(Boolean)
}

export function rowMsGroupTokens(row: ClientQueryRow): string[] {
  const fromTags = Array.isArray(row.tags) ? row.tags.map(String) : []
  const fromMs = splitGroupTokens(String(row.ms_groups || row.groups || ''))
  const seen = new Set<string>()
  const out: string[] = []
  for (const name of [...fromTags, ...fromMs]) {
    const key = normalizeGroupKey(name)
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    out.push(key)
  }
  return out
}

export function rowAiGroupTokens(row: ClientQueryRow): string[] {
  const fromAi = Array.isArray(row.ai_groups) ? row.ai_groups.map(String) : []
  const seen = new Set<string>()
  const out: string[] = []
  for (const name of fromAi) {
    const key = normalizeGroupKey(name)
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    out.push(key)
  }
  return out
}

/** Mirrors plugins/moysklad/groups.row_has_group for public ClientRow. */
export function rowMatchesGroupFilter(
  row: ClientQueryRow,
  group: string,
  groupSource: string = 'any'
): boolean {
  const target = normalizeGroupKey(group)
  if (!target) {
    return true
  }
  const src = String(groupSource || 'any')
    .trim()
    .toLowerCase()
    .replace(/ё/g, 'е')
  let tokens: string[]
  if (src === 'ms' || src === 'moysklad' || src === 'мойсклад') {
    tokens = rowMsGroupTokens(row)
  } else if (src === 'ai' || src === 'ии' || src === 'llm') {
    tokens = rowAiGroupTokens(row)
  } else {
    tokens = [...rowMsGroupTokens(row), ...rowAiGroupTokens(row)]
  }
  return tokens.includes(target)
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
    ...(Array.isArray(row.ai_groups) ? row.ai_groups : []),
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

export function filterClientRowsByAudience<T extends ClientQueryRow>(
  rows: T[],
  opts: { q?: string; group?: string; groupSource?: string }
): T[] {
  let out = rows
  const group = String(opts.group || '').trim()
  if (group) {
    out = out.filter(row => rowMatchesGroupFilter(row, group, opts.groupSource || 'any'))
  }
  const q = String(opts.q || '').trim()
  if (q) {
    out = filterClientRowsByQuery(out, q)
  }
  return out
}

/**
 * Prefer exact cache hit; else filter unfiltered (empty group/q) snapshots
 * by group + q so chip clicks update contacts instantly.
 */
export function pickLocalClientsSeed<T>(opts: {
  q: string
  group?: string
  groupSource?: string
  readExact: () => T | null
  /** Same tabs, empty q, same group (exact group page without search). */
  readBase: () => T | null
  /** Unfiltered bases (group='') to locally apply group+q. */
  readUnfilteredBases: () => Array<T | null>
  filterRows: (seed: T, q: string, group: string, groupSource: string) => T | null
}): T | null {
  const exact = opts.readExact()
  if (exact) {
    return exact
  }

  const q = String(opts.q || '').trim()
  const group = String(opts.group || '').trim()
  const groupSource = String(opts.groupSource || 'any')

  const base = opts.readBase()
  if (base && !q) {
    return base
  }
  if (base && q) {
    const filtered = opts.filterRows(base, q, group, groupSource)
    if (filtered) {
      return filtered
    }
  }

  for (const seed of opts.readUnfilteredBases()) {
    if (!seed) {
      continue
    }
    const filtered = opts.filterRows(seed, q, group, groupSource)
    if (filtered) {
      return filtered
    }
  }
  return null
}
