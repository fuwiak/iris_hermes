/** Pure TG status labels — only two values (file + Клиенты «Статус»). */

export type TgActiveStatusWord = 'АКТИВНЫЙ' | 'НЕАКТИВНЫЙ'

/** Binary TG status: green АКТИВНЫЙ / red НЕАКТИВНЫЙ. No third state. */
export function tgActiveStatusWord(row: {
  tg_active?: boolean | null
}): TgActiveStatusWord {
  return row.tg_active === true ? 'АКТИВНЫЙ' : 'НЕАКТИВНЫЙ'
}

export function tgActiveCellTitle(row: {
  tg_active?: boolean | null
  tg_active_label?: string | null
  tg_active_detail?: string | null
  tg_active_nick?: string | null
  tg_nick?: string | null
}): string {
  const detail = String(row.tg_active_detail || row.tg_active_label || '').trim()
  if (row.tg_active === true) {
    const nick = row.tg_active_nick || row.tg_nick || ''
    if (detail) {
      return detail
    }
    return nick ? `Есть Telegram · ${nick}` : 'Есть Telegram'
  }
  return detail || 'Нет активного Telegram'
}

/** Map UI filter selection → API ``tg_status`` query. */
export function tgStatusFilterParam(opts: {
  selected?: string[] | null
  query?: string
}): '' | 'active' | 'inactive' {
  const selected = opts.selected
  const q = String(opts.query || '')
    .trim()
    .toLowerCase()
    .replace(/ё/g, 'е')

  if (selected != null && selected.length > 0) {
    const hasActive = selected.includes('АКТИВНЫЙ')
    const hasInactive = selected.includes('НЕАКТИВНЫЙ')
    if (hasActive && !hasInactive) {
      return 'active'
    }
    if (hasInactive && !hasActive) {
      return 'inactive'
    }
    return ''
  }

  if (!q) {
    return ''
  }
  if (q.includes('неактив')) {
    return 'inactive'
  }
  if (q.includes('актив')) {
    return 'active'
  }
  return ''
}
