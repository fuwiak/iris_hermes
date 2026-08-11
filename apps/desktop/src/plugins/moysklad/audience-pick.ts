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
}

/**
 * Clicking an audience chip must always focus compose on that client.
 * Multi mode accumulates ids; it must NOT skip applyClientSelectionUi.
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
      channel
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
    channel
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
