/**
 * Pure helpers for MoySklad mass Рассылки send + reply collection.
 * Chunking stays client-side so HTTP stays under Bot API / gateway timeouts.
 */

/** Matches backend ``MASS_SEND_BATCH_MAX`` on mark-sent-batch. */
export const MASS_SEND_CHUNK = 100

/** Soft cap when «Выбрать всю аудиторию» via ``/clients/ids`` (10k blast). */
export const MASS_AUDIENCE_SELECT_CAP = 10000

/** Ask for confirm before firing this many recipients. */
export const MASS_SEND_CONFIRM_AT = 20

/**
 * Scale posture for Рассылки (not Kubernetes):
 * - One shared draft → mark-sent-batch chunks (cheapest 10k path).
 * - Per-client AI personalize → backlog of chunks + draft cache (queue mindset).
 * - Filters stay fast via stamped catalog indexes on the server.
 */
export const MASS_SEND_SCALE_HINT =
  'До 10 тыс.: один общий текст пачками по 100. AI на каждого — очередь чанков + кэш черновиков, не 10k синхронных LLM.'

export type MassSendStep = 1 | 2 | 3 | 4

export const MASS_SEND_STEP_LABELS: Record<MassSendStep, string> = {
  1: 'Аудитория',
  2: 'Текст',
  3: 'Отправка',
  4: 'Ответы'
}

export function chunkIds(ids: string[], size = MASS_SEND_CHUNK): string[][] {
  const clean = [...new Set(ids.map(id => String(id || '').trim()).filter(Boolean))]
  const chunkSize = Math.max(1, Math.floor(size) || MASS_SEND_CHUNK)
  const out: string[][] = []
  for (let i = 0; i < clean.length; i += chunkSize) {
    out.push(clean.slice(i, i + chunkSize))
  }
  return out
}

export function massSendProgressLabel(done: number, total: number, chunkLen: number): string {
  const safeTotal = Math.max(0, total)
  const safeDone = Math.max(0, Math.min(done, safeTotal))
  if (safeTotal <= 0) {
    return 'Нет получателей'
  }
  const nextEnd = Math.min(safeDone + Math.max(0, chunkLen), safeTotal)
  if (safeDone >= safeTotal) {
    return `Готово: ${safeTotal}/${safeTotal}`
  }
  return `Отправка ${safeDone + 1}–${nextEnd} из ${safeTotal}…`
}

export function needsMassSendConfirm(count: number, threshold = MASS_SEND_CONFIRM_AT): boolean {
  return count >= threshold
}

export function massSendConfirmText(count: number): string {
  return (
    `Отправить одно и то же сообщение ${count} клиентам?\n\n` +
    `Отправка идёт пачками по ${MASS_SEND_CHUNK} (до ${MASS_AUDIENCE_SELECT_CAP}). ` +
    `Ответы потом собираются кнопкой «Собрать ответы» (TG conversation).\n` +
    MASS_SEND_SCALE_HINT
  )
}

export function mergeUniqueIds(existing: string[], next: string[]): string[] {
  const seen = new Set(existing.map(id => String(id || '').trim()).filter(Boolean))
  const out = [...seen]
  for (const raw of next) {
    const id = String(raw || '').trim()
    if (!id || seen.has(id)) {
      continue
    }
    seen.add(id)
    out.push(id)
  }
  return out
}

/** One recipient row of a background mass-send job (server snapshot). */
export interface MassRecipientRow {
  client_id?: string
  client_name?: string
  tg_nick?: string
  status?: string
  error?: string | null
  detail?: string | null
  ts?: string | null
}

/** Poll summary of a background mass-send job. */
export interface MassJobSummary {
  id?: string
  status?: string
  channel?: string
  total?: number
  attempted?: number
  sent_ok?: number
  sent_failed?: number
  created_at?: string
  started_at?: string | null
  finished_at?: string | null
  cancel_requested?: boolean
  error?: string | null
  message_preview?: string
  /** Present on conversation-derived history rows (Telegram export / Facts). */
  history_kind?: string
}

export const MASS_TERMINAL_ROW_STATUSES = new Set(['ok', 'failed', 'skipped'])

export function isMassJobActive(status?: string): boolean {
  return status === 'running' || status === 'queued'
}

/** Rows finalize in send order — poll offset = length of the terminal prefix. */
export function terminalPrefixLength(rows: MassRecipientRow[]): number {
  let n = 0
  for (const row of rows) {
    if (!MASS_TERMINAL_ROW_STATUSES.has(String(row?.status || ''))) {
      break
    }
    n += 1
  }
  return n
}

/** Overlay a freshly polled slice at `offset` onto the locally cached rows. */
export function overlayMassRows(
  prev: MassRecipientRow[],
  incoming: MassRecipientRow[],
  offset: number
): MassRecipientRow[] {
  if (!incoming.length) {
    return prev
  }
  const at = Math.max(0, Math.min(Math.floor(offset) || 0, prev.length))
  const out = prev.slice(0, at)
  out.push(...incoming)
  const tailStart = at + incoming.length
  if (prev.length > tailStart) {
    out.push(...prev.slice(tailStart))
  }
  return out
}

export function massJobPercent(attempted: number, total: number): number {
  if (!total || total <= 0) {
    return 0
  }
  return Math.max(0, Math.min(100, Math.round((attempted / total) * 100)))
}

export function massRecipientDisplay(row: MassRecipientRow): string {
  const nick = String(row.tg_nick || '').replace(/^@/, '')
  return (
    String(row.client_name || '').trim() ||
    (nick ? `@${nick}` : '') ||
    String(row.client_id || '').trim() ||
    '—'
  )
}

export function massRowStatusLabel(status?: string): string {
  switch (String(status || '')) {
    case 'ok':
      return '✓ отправлено'
    case 'failed':
      return '✕ ошибка'
    case 'sending':
      return '→ отправляем…'
    case 'skipped':
      return '– пропущен'
    default:
      return '⏳ в очереди'
  }
}

export function massJobStatusLabel(status?: string): string {
  switch (String(status || '')) {
    case 'queued':
      return 'в очереди'
    case 'running':
      return 'идёт'
    case 'done':
      return 'завершена'
    case 'cancelled':
      return 'остановлена'
    case 'failed':
      return 'упала с ошибкой'
    case 'interrupted':
      return 'прервана (сервер перезапускался)'
    default:
      return String(status || '—')
  }
}

/** Which mass-mail step the operator should do next. */
export function resolveMassSendStep(input: {
  selectedCount: number
  hasDraft: boolean
  sentCount: number
}): MassSendStep {
  if (input.selectedCount <= 0) {
    return 1
  }
  if (!input.hasDraft) {
    return 2
  }
  if (input.sentCount <= 0) {
    return 3
  }
  return 4
}

export function massSendStepHint(
  step: MassSendStep,
  ctx: {
    audience: number
    selectedCount: number
    chunk: number
  }
): string {
  switch (step) {
    case 1:
      return ctx.audience > 0
        ? `В фильтре ${ctx.audience} чел. Нажмите «Выбрать всех» — не кликайте чипы по одному.`
        : 'Сузьте фильтры выше, пока matched_total > 0.'
    case 2:
      return `Получателей: ${ctx.selectedCount}. Напишите общий текст ниже (или AI).`
    case 3:
      return `Готово к отправке ${ctx.selectedCount} чел. пачками по ${ctx.chunk}.`
    case 4:
      return 'Рассылка ушла. Соберите входящие — кто ответил, ждёт вашего ответа.'
  }
}
