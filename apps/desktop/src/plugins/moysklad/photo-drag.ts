/**
 * Photo placement inside the message composer.
 *
 * The preview is absolutely positioned and dragged by pointer, but it must
 * never cover message text — Telegram caption layout is free-form in the
 * composer, so we park the photo only on empty lines and expand a blank run
 * tall enough for the preview. Delivery is unchanged: Telegram still sends
 * the photo above the caption regardless of where the preview sits.
 *
 * CSS must keep the preview smaller than the box (see .ms-msg-photo max-width)
 * — otherwise clampPhotoOffset pins maxX/maxY to 0 and drag feels broken.
 */

export interface PhotoOffset {
  x: number
  y: number
}

export interface PhotoDragBounds {
  boxW: number
  boxH: number
  photoW: number
  photoH: number
}

export interface PhotoDragStart {
  pointerX: number
  pointerY: number
  offsetX: number
  offsetY: number
}

/** Textarea metrics used to map Y ↔ line index. */
export interface LineMetrics {
  lineHeight: number
  paddingTop: number
}

export const PHOTO_ORIGIN: PhotoOffset = { x: 0, y: 0 }

/**
 * Keep the photo inside the message box.
 *
 * When the photo is larger than the box on an axis there is no slack, so that
 * axis pins to 0 instead of going negative — dragging must never push part of
 * the picture out of view where the ✕ becomes unreachable.
 */
export function clampPhotoOffset(
  offset: PhotoOffset,
  bounds: PhotoDragBounds
): PhotoOffset {
  const maxX = Math.max(0, bounds.boxW - bounds.photoW)
  const maxY = Math.max(0, bounds.boxH - bounds.photoH)
  const x = Number.isFinite(offset.x) ? offset.x : 0
  const y = Number.isFinite(offset.y) ? offset.y : 0
  return {
    x: Math.min(Math.max(x, 0), maxX),
    y: Math.min(Math.max(y, 0), maxY)
  }
}

/** Offset for the current pointer position, clamped to the box. */
export function dragPhotoOffset(
  start: PhotoDragStart,
  pointer: { x: number; y: number },
  bounds: PhotoDragBounds
): PhotoOffset {
  return clampPhotoOffset(
    {
      x: start.offsetX + (pointer.x - start.pointerX),
      y: start.offsetY + (pointer.y - start.pointerY)
    },
    bounds
  )
}

/**
 * Re-clamp a stored offset after the box or photo resizes (window resize, a
 * taller draft, a different picture). Without this the photo would hang
 * outside the box after the layout shrinks under it.
 */
export function reflowPhotoOffset(
  offset: PhotoOffset,
  bounds: PhotoDragBounds
): PhotoOffset {
  return clampPhotoOffset(offset, bounds)
}

/** Line indices that are empty (trim === ''), plus a virtual slot after the last line. */
export function emptyLineSlotIndices(text: string): number[] {
  const lines = text.split('\n')
  const slots: number[] = []
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() === '') {
      slots.push(i)
    }
  }
  slots.push(lines.length)
  return slots
}

export function photoYForLine(lineIndex: number, metrics: LineMetrics): number {
  const lh = Math.max(1, metrics.lineHeight)
  const pad = Number.isFinite(metrics.paddingTop) ? metrics.paddingTop : 0
  return pad + Math.max(0, lineIndex) * lh
}

export function lineIndexAtY(y: number, metrics: LineMetrics): number {
  const lh = Math.max(1, metrics.lineHeight)
  const pad = Number.isFinite(metrics.paddingTop) ? metrics.paddingTop : 0
  return Math.max(0, Math.floor((y - pad) / lh))
}

/** How many blank lines clear `photoH` at the given line height. */
export function blankLinesForPhotoHeight(photoH: number, lineHeight: number): number {
  const lh = Math.max(1, lineHeight)
  const h = Number.isFinite(photoH) && photoH > 0 ? photoH : lh
  return Math.max(1, Math.ceil(h / lh))
}

/**
 * Expand/create a run of blank lines at `slotIndex` so the photo has room and
 * does not cover text. Returns the new text and the start index of that run.
 */
export function ensureBlankRunAtSlot(
  text: string,
  slotIndex: number,
  blankCount: number
): { text: string; slotIndex: number } {
  const lines = text.split('\n')
  const need = Math.max(1, blankCount)
  let idx = Math.max(0, Math.min(slotIndex, lines.length))

  // Dropping on a non-empty line → open a gap before it.
  if (idx < lines.length && lines[idx].trim() !== '') {
    lines.splice(idx, 0, ...Array.from({ length: need }, () => ''))
    return { text: lines.join('\n'), slotIndex: idx }
  }

  if (idx >= lines.length) {
    let trailing = 0
    for (let i = lines.length - 1; i >= 0 && lines[i].trim() === ''; i--) {
      trailing++
    }
    const missing = need - trailing
    if (missing > 0) {
      for (let i = 0; i < missing; i++) {
        lines.push('')
      }
    }
    const start = lines.length - Math.max(need, trailing + Math.max(0, missing))
    return { text: lines.join('\n'), slotIndex: Math.max(0, start) }
  }

  let start = idx
  while (start > 0 && lines[start - 1].trim() === '') {
    start--
  }
  let end = idx
  while (end < lines.length - 1 && lines[end + 1].trim() === '') {
    end++
  }
  const have = end - start + 1
  if (have < need) {
    lines.splice(end + 1, 0, ...Array.from({ length: need - have }, () => ''))
  }
  return { text: lines.join('\n'), slotIndex: start }
}

/**
 * Snap a free-drag offset onto the nearest *existing* empty-line slot so the
 * preview never rests on a filled line mid-gesture.
 */
export function snapPhotoToEmptyLine(
  offset: PhotoOffset,
  text: string,
  metrics: LineMetrics,
  bounds: PhotoDragBounds
): PhotoOffset {
  const slots = emptyLineSlotIndices(text)
  let best = slots[0] ?? 0
  let bestDist = Infinity
  for (const slot of slots) {
    const sy = photoYForLine(slot, metrics)
    const d = Math.abs(offset.y - sy)
    if (d < bestDist) {
      bestDist = d
      best = slot
    }
  }
  return clampPhotoOffset({ x: offset.x, y: photoYForLine(best, metrics) }, bounds)
}

/**
 * Final park after a drag: pick the line under the pointer, open/expand a blank
 * run tall enough for the photo, and clamp the offset onto that run.
 */
export function parkPhotoAtY(
  text: string,
  y: number,
  metrics: LineMetrics,
  photoH: number,
  bounds: PhotoDragBounds,
  offsetX: number
): { text: string; offset: PhotoOffset; slotIndex: number } {
  const lineIdx = lineIndexAtY(y, metrics)
  const need = blankLinesForPhotoHeight(photoH, metrics.lineHeight)
  const parked = ensureBlankRunAtSlot(text, lineIdx, need)
  const offset = clampPhotoOffset(
    { x: offsetX, y: photoYForLine(parked.slotIndex, metrics) },
    bounds
  )
  return { text: parked.text, offset, slotIndex: parked.slotIndex }
}
