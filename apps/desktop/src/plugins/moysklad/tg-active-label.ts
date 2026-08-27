/** Pure TG status labels shared by Клиенты table + Рассылки chips. */

export type TgActiveStatusWord = 'АКТИВНЫЙ' | 'НЕАКТИВНЫЙ' | 'НЕ ПРОВЕРЕН'

export function tgActiveStatusWord(row: {
  tg_active?: boolean | null
}): TgActiveStatusWord {
  if (row.tg_active === true) {
    return 'АКТИВНЫЙ'
  }
  if (row.tg_active === false) {
    return 'НЕАКТИВНЫЙ'
  }
  return 'НЕ ПРОВЕРЕН'
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
  if (row.tg_active === false) {
    return detail || 'Нет активного Telegram по номеру'
  }
  return detail || 'Ещё не проверен'
}
