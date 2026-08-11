/**
 * Pure helpers for MoySklad Рассылки audience chip selection.
 * Kept outside plugin.tsx so vitest can prove clicks focus compose + filters.
 */

export type AudiencePickMode = 'single' | 'multi'

export interface AudiencePickInput {
  pickMode: AudiencePickMode
  /** MoySklad / contact id from the chip. Empty → no-op. */
  rowId: string
  rowName?: string
  rowPhone?: string
  rowTgNick?: string
  selectedIds: string[]
}

export interface AudiencePickResult {
  /** False when the chip has no usable id — UI must not silently ignore. */
  ok: boolean
  reason?: 'missing_id'
  selectedIds: string[]
  /** Client id that must drive the compose panel (facts / draft). */
  focusId: string
  focusName: string
  channel: 'telegram' | 'whatsapp'
  /** Always true: Facts panel must load immediately on chip click. */
  loadFacts: true
}

/**
 * Clicking an audience chip must always focus compose on that client.
 * Multi mode accumulates ids; it must NOT skip applyClientSelectionUi.
 * Facts panel must open immediately (loadFacts) — not wait for AI generate.
 */
export function planAudienceChipClick(input: AudiencePickInput): AudiencePickResult {
  const rowId = String(input.rowId || '').trim()
  const focusName = String(input.rowName || '').trim()
  const phone = String(input.rowPhone || '').trim()
  const tgNick = String(input.rowTgNick || '').trim()
  const channel: 'telegram' | 'whatsapp' =
    phone && !tgNick ? 'whatsapp' : 'telegram'

  if (!rowId) {
    return {
      ok: false,
      reason: 'missing_id',
      selectedIds: input.selectedIds,
      focusId: '',
      focusName,
      channel,
      loadFacts: true
    }
  }

  let selectedIds: string[]
  if (input.pickMode === 'multi') {
    // Accumulate; do not toggle-off on re-click (reset button clears).
    selectedIds = input.selectedIds.includes(rowId)
      ? input.selectedIds
      : [...input.selectedIds, rowId]
  } else {
    selectedIds = [rowId]
  }

  return {
    ok: true,
    selectedIds,
    focusId: rowId,
    focusName,
    channel,
    loadFacts: true
  }
}

/** Seed Facts panel from the audience chip row before /clients/{id} returns. */
export function seedFactsFromAudienceRow(row: {
  id?: string | null
  name?: string | null
  phone?: string | null
  email?: string | null
  tg_nick?: string | null
  sales_type?: string | null
  channels?: string[] | null
  channel?: string | null
  order_count?: number | null
  avg_check?: number | null
  last_order_at?: string | null
  tags?: string[] | null
}): {
  client_id?: string
  name?: string
  phone?: string
  email?: string
  tg_nick?: string
  sales_type?: string
  channels?: string[]
  primary_channel?: string
  order_count?: number
  avg_check?: number
  last_order?: { date?: string }
  tags?: string[]
} {
  const channels = Array.isArray(row.channels)
    ? row.channels.filter(Boolean).map(String)
    : row.channel
      ? String(row.channel)
          .split(',')
          .map(s => s.trim())
          .filter(Boolean)
      : []
  return {
    client_id: String(row.id || '').trim() || undefined,
    name: String(row.name || '').trim() || undefined,
    phone: String(row.phone || '').trim() || undefined,
    email: String(row.email || '').trim() || undefined,
    tg_nick: String(row.tg_nick || '').trim() || undefined,
    sales_type: String(row.sales_type || '').trim() || undefined,
    channels,
    primary_channel: channels[0],
    order_count: row.order_count == null ? undefined : Number(row.order_count),
    avg_check: row.avg_check == null ? undefined : Number(row.avg_check),
    last_order: row.last_order_at
      ? { date: String(row.last_order_at) }
      : undefined,
    tags: Array.isArray(row.tags) ? row.tags.map(String) : undefined
  }
}

/** Sales-tab filter ids used by Клиенты / Рассылки. */
export const SALES_FILTER_IDS = ['all', 'marketplace', 'direct'] as const
export type SalesFilterId = (typeof SALES_FILTER_IDS)[number]

export function isSalesFilterId(value: string): value is SalesFilterId {
  return (SALES_FILTER_IDS as readonly string[]).includes(value)
}

/**
 * Filter tabs must stay clickable while a list reload is in flight —
 * otherwise search debounce / SWR leaves the UI feeling «фильтры не работают».
 */
export function salesFilterTabsDisabled(opts: {
  loading: boolean
  hasCounts: boolean
}): boolean {
  // Only block the very first paint with no data yet.
  return Boolean(opts.loading && !opts.hasCounts)
}
