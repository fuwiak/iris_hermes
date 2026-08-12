/**
 * Pure helpers for MoySklad mass Рассылки send + reply collection.
 * Chunking stays client-side so HTTP stays under Bot API / gateway timeouts.
 */

/** Matches backend ``max N clients per batch`` on mark-sent-batch. */
export const MASS_SEND_CHUNK = 50

/** Soft cap when «Выбрать всю аудиторию» paginates /clients. */
export const MASS_AUDIENCE_SELECT_CAP = 5000

/** Ask for confirm before firing this many recipients. */
export const MASS_SEND_CONFIRM_AT = 20

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
    `Отправка идёт пачками по ${MASS_SEND_CHUNK}. ` +
    `Ответы потом собираются кнопкой «Собрать ответы» (TG conversation).`
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
