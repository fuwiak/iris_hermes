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

/** Server catalog is rebuilding (cold start after deploy) — retry, not a dead end. */
export function isCatalogWarmingError(err: unknown): boolean {
  const text = err instanceof Error ? err.message : String(err ?? '')
  return /503|rebuilding|retry shortly|catalog unavailable/i.test(text)
}

/**
 * Backoff for audience reload attempts while the server catalog warms.
 * Append pages stay snappy; the first (replace) load stretches to ~4s steps
 * so a full post-deploy rebuild (≈1–2 min) is survivable without a dead UI.
 */
export function audienceRetryDelayMs(attempt: number, append: boolean): number {
  const step = Math.max(0, Math.floor(attempt) || 0)
  if (append) {
    return 400 + step * 200
  }
  return Math.min(1000 + step * 500, 4000)
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
  sales_type?: string | null
  vip?: boolean | null
  audience?: { direct?: boolean; marketplace?: boolean } | null
  tg_conversation?: string | null
  tg_conversation_preview?: string | null
  actual_address?: string | null
}

const VIP_RE = /\b(vip|вип)\b/i

/** Mirrors plugins/moysklad/audience.row_is_vip for public ClientRow. */
export function rowLooksVip(row: ClientQueryRow): boolean {
  if (row.vip) {
    return true
  }
  const blob = [
    ...(row.tags || []),
    row.state,
    row.groups,
    row.ms_groups,
    ...(row.ai_groups || [])
  ]
    .map(x => String(x || ''))
    .join(' ')
  return VIP_RE.test(blob)
}

/** Mirrors sales_filter tabs: all / direct / marketplace. */
export function rowMatchesSalesFilter(
  row: ClientQueryRow,
  salesFilter: string
): boolean {
  const key = String(salesFilter || 'all')
    .trim()
    .toLowerCase()
    .replace(/ё/g, 'е')
  if (!key || key === 'all' || key === 'any') {
    return true
  }
  const aud = row.audience
  if (aud && typeof aud === 'object') {
    if (key === 'direct' || key === 'прямые') {
      return Boolean(aud.direct) && !aud.marketplace
    }
    if (key === 'marketplace' || key === 'маркетплейс') {
      return Boolean(aud.marketplace)
    }
  }
  const st = String(row.sales_type || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
  if (key === 'direct' || key === 'прямые') {
    return st.includes('прям') && !st.includes('маркет')
  }
  if (key === 'marketplace' || key === 'маркетплейс') {
    return st.includes('маркет') || st.includes('flow') || st.includes('флау')
  }
  return true
}

/** Mirrors plugins/moysklad/audience.row_matches_channel_kind. */
export function rowMatchesChannelKind(
  row: ClientQueryRow,
  channelKind: string
): boolean {
  const key = String(channelKind || '')
    .trim()
    .toLowerCase()
  if (!key || key === 'any' || key === 'all') {
    return true
  }
  const nick = String(row.tg_nick || '').trim()
  const phone = digitsPhone(String(row.phone || ''))
  const blob = clientSalesChannelTokens(row).join(' ').toLowerCase()
  if (key === 'telegram' || key === 'tg') {
    return Boolean(nick) || blob.includes('telegram') || blob.includes('телеграм')
  }
  if (key === 'whatsapp' || key === 'wa' || key === 'max') {
    return (
      Boolean(phone) ||
      /whatsapp|watsapp|ватсап|вотсап|\bmax\b|макс/.test(blob)
    )
  }
  return true
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
  opts: {
    q?: string
    group?: string
    groupSource?: string
    salesFilter?: string
    channelKind?: string
    requirePhone?: boolean
    requireTelegram?: boolean
    vipOnly?: boolean
    birthdaySoon?: boolean
  }
): T[] {
  let out = rows
  const salesFilter = String(opts.salesFilter || 'all').trim()
  if (salesFilter && salesFilter !== 'all') {
    out = out.filter(row => rowMatchesSalesFilter(row, salesFilter))
  }
  const channelKind = String(opts.channelKind || '').trim()
  if (channelKind) {
    out = out.filter(row => rowMatchesChannelKind(row, channelKind))
  }
  if (opts.requirePhone) {
    out = out.filter(row => Boolean(digitsPhone(String(row.phone || ''))))
  }
  if (opts.requireTelegram) {
    out = out.filter(row => Boolean(String(row.tg_nick || '').trim()))
  }
  if (opts.vipOnly) {
    out = out.filter(row => rowLooksVip(row))
  }
  if (opts.birthdaySoon) {
    out = out.filter(row => {
      const blob = [
        ...(row.tags || []),
        row.groups,
        row.ms_groups,
        ...(row.ai_groups || [])
      ]
        .map(x => String(x || ''))
        .join(' ')
        .toLowerCase()
        .replace(/ё/g, 'е')
      return /день\s*рожден|др\b|birthday|событие/.test(blob)
    })
  }
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
 * Yield rows in chunks so the UI can paint progressively without freezing.
 * Default: whole array in one chunk (one optional frame yield) — per-row rAF
 * made large catalogs crawl («Подгружаем клиентов… 24 / 9507»).
 */
export async function forEachRowProgressive<T>(
  rows: T[],
  onRow: (row: T, index: number) => void | Promise<void>,
  opts?: {
    isCancelled?: () => boolean
    /** Frame gap between chunks; 0 = next microtask / rAF only. */
    delayMs?: number
    /** Rows per yield. Default = all rows (single paint burst). */
    chunkSize?: number
  }
): Promise<number> {
  const delayMs = opts?.delayMs ?? 0
  const chunkSize = Math.max(1, opts?.chunkSize ?? Math.max(rows.length, 1))
  let painted = 0
  for (let i = 0; i < rows.length; i++) {
    if (opts?.isCancelled?.()) {
      break
    }
    await onRow(rows[i], i)
    painted += 1
    if (painted % chunkSize !== 0 && i + 1 < rows.length) {
      continue
    }
    if (delayMs > 0) {
      await new Promise<void>(resolve => {
        window.setTimeout(resolve, delayMs)
      })
    } else if (typeof requestAnimationFrame === 'function') {
      await new Promise<void>(resolve => {
        requestAnimationFrame(() => resolve())
      })
    } else {
      await Promise.resolve()
    }
  }
  return painted
}

const NO_SALES_CHANNEL = 'Без канала'

/** Individual MoySklad sales channels for a client row (not joined display). */
export function clientSalesChannelTokens(row: ClientQueryRow): string[] {
  const fromList = (row.channels || [])
    .map(c => String(c || '').trim())
    .filter(c => c && c !== NO_SALES_CHANNEL)
  if (fromList.length) {
    return fromList
  }
  return String(row.channel || '')
    .split(',')
    .map(s => s.trim())
    .filter(c => c && c !== NO_SALES_CHANNEL)
}

export function rowMatchesSalesChannelColumnFilter(
  row: ClientQueryRow,
  query: string,
  selected: string[] | null,
  blankLabel: string
): boolean {
  const tokens = clientSalesChannelTokens(row)
  const labels = tokens.length > 0 ? tokens : [blankLabel]
  const displayJoined = tokens.length > 0 ? tokens.join(', ') : NO_SALES_CHANNEL

  if (query.trim()) {
    const q = query.trim().toLowerCase()
    const blob = `${displayJoined} ${labels.join(' ')}`.toLowerCase()
    if (!blob.includes(q)) {
      return false
    }
  }
  if (selected != null) {
    const picked = labels.map(c => (c === '' || c === '—' ? blankLabel : c))
    return picked.some(l => selected.includes(l))
  }
  return true
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
