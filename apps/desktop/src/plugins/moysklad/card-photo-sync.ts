/**
 * Re-attach a card photo to a draft that lost it.
 *
 * The composer keeps the photo in React state (`sendImage`), but a draft is
 * persisted server-side as *text only* (`set_outreach_draft` stores `message`).
 * So every path that fills the textarea from text — restoring a draft when you
 * pick another client, `Сгенерировать AI`, `Букет из истории` — brought back
 * the «Card» blocks with no picture, and the send went out text-only.
 *
 * Card blocks always open with the title in guillemets (see `cardMessageBlock`),
 * so the draft itself says which cards it sells. Match those titles against the
 * marketplace catalog and hand the photo back.
 */

export interface CardPhotoLike {
  name?: string
  image?: string
}

export interface DraftPhoto {
  name: string
  url: string
}

/** Fold a card title the way the backend does (`_normalize_card_name`). */
export function normalizeCardName(name: string): string {
  return String(name || '')
    .toLowerCase()
    .replace(/[^0-9a-zа-яё]+/gi, ' ')
    .trim()
    .replace(/\s+/g, ' ')
}

/** Card titles quoted in a draft, in the order they appear. */
export function cardNamesInText(text: string): string[] {
  const out: string[] = []

  for (const match of String(text || '').matchAll(/«([^«»]{1,200})»/g)) {
    const name = match[1].trim()

    if (name) {
      out.push(name)
    }
  }

  return out
}

/**
 * Photo for a draft, or null when nothing matches.
 *
 * The LAST quoted card wins — same rule as `addCardToMessage`, where a freshly
 * appended card takes over the single photo slot.
 */
export function photoForDraftText(
  text: string,
  cards: readonly CardPhotoLike[] | null | undefined
): DraftPhoto | null {
  const names = cardNamesInText(text)

  if (!names.length || !cards || !cards.length) {
    return null
  }

  const byName = new Map<string, CardPhotoLike>()

  for (const card of cards) {
    const key = normalizeCardName(card.name || '')

    if (key && card.image && !byName.has(key)) {
      byName.set(key, card)
    }
  }

  for (let i = names.length - 1; i >= 0; i -= 1) {
    const card = byName.get(normalizeCardName(names[i]))

    if (card?.image) {
      return { name: card.name || names[i], url: card.image }
    }
  }

  return null
}

/**
 * Does this draft still sell the attached card? A photo left over from a card
 * the seller deleted out of the text must not ride along to the next client.
 */
export function draftKeepsPhoto(
  text: string,
  photo: { name?: string; url?: string; dataUrl?: string } | null | undefined,
  cards: readonly CardPhotoLike[] | null | undefined
): boolean {
  if (!photo) {
    return false
  }
  // Uploads and hand-picked pictures are not tied to any card block.
  if (photo.dataUrl || !photo.url) {
    return true
  }

  const known = (cards || []).some(card => card.image && card.image === photo.url)

  if (!known) {
    return true
  }

  const wanted = normalizeCardName(photo.name || '')

  return cardNamesInText(text).some(name => normalizeCardName(name) === wanted)
}
