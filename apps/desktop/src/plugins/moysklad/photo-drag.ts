/**
 * Free placement of the attached photo inside the message composer.
 *
 * The photo preview used to be pinned above the textarea, so it always sat on
 * the first line and pushed the text down. Here it is absolutely positioned
 * inside the message box and dragged by pointer, which keeps the geometry out
 * of plugin.tsx and lets the clamping be tested on its own.
 *
 * Placement is composer-only: Telegram always renders the photo above the
 * caption, so where the operator parks the preview does not change delivery.
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
