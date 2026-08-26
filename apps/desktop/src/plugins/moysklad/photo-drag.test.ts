import { describe, expect, it } from 'vitest'

import {
  clampPhotoOffset,
  dragPhotoOffset,
  PHOTO_ORIGIN,
  reflowPhotoOffset
} from './photo-drag'

const BOUNDS = { boxW: 600, boxH: 400, photoW: 200, photoH: 150 }

describe('clampPhotoOffset', () => {
  it('keeps an in-range offset untouched', () => {
    expect(clampPhotoOffset({ x: 120, y: 80 }, BOUNDS)).toEqual({ x: 120, y: 80 })
  })

  it('clamps to the box on both axes', () => {
    expect(clampPhotoOffset({ x: 9999, y: 9999 }, BOUNDS)).toEqual({ x: 400, y: 250 })
    expect(clampPhotoOffset({ x: -50, y: -50 }, BOUNDS)).toEqual(PHOTO_ORIGIN)
  })

  it('pins the axis where the photo is larger than the box', () => {
    const wide = { boxW: 200, boxH: 400, photoW: 320, photoH: 150 }
    // No horizontal slack — the photo must not be pushed out of view.
    expect(clampPhotoOffset({ x: 60, y: 30 }, wide)).toEqual({ x: 0, y: 30 })
    expect(clampPhotoOffset({ x: -60, y: 30 }, wide)).toEqual({ x: 0, y: 30 })
  })

  it('falls back to the origin for non-finite input', () => {
    expect(clampPhotoOffset({ x: NaN, y: 40 }, BOUNDS)).toEqual({ x: 0, y: 40 })
    expect(clampPhotoOffset({ x: 10, y: Infinity }, BOUNDS)).toEqual({ x: 10, y: 0 })
  })

  it('leaves room to drag when the preview is smaller than the box (UI contract)', () => {
    // Mirrors .ms-msg-photo { max-width: 42% } + max-height 160px on a 600×400 box.
    const preview = { boxW: 600, boxH: 400, photoW: 180, photoH: 160 }
    const parked = clampPhotoOffset({ x: 200, y: 100 }, preview)
    expect(parked).toEqual({ x: 200, y: 100 })
    expect(
      dragPhotoOffset(
        { pointerX: 100, pointerY: 100, offsetX: 0, offsetY: 0 },
        { x: 250, y: 180 },
        preview
      )
    ).toEqual({ x: 150, y: 80 })
  })
})

describe('dragPhotoOffset', () => {
  const start = { pointerX: 300, pointerY: 200, offsetX: 100, offsetY: 50 }

  it('moves the photo by the pointer delta', () => {
    expect(dragPhotoOffset(start, { x: 340, y: 230 }, BOUNDS)).toEqual({ x: 140, y: 80 })
    expect(dragPhotoOffset(start, { x: 260, y: 170 }, BOUNDS)).toEqual({ x: 60, y: 20 })
  })

  it('grabbing anywhere on the photo keeps it under the cursor', () => {
    // Same pointer travel from a different grab point yields the same delta.
    const other = { pointerX: 10, pointerY: 10, offsetX: 100, offsetY: 50 }
    expect(dragPhotoOffset(other, { x: 50, y: 40 }, BOUNDS)).toEqual({ x: 140, y: 80 })
  })

  it('stops at the box edges instead of escaping', () => {
    expect(dragPhotoOffset(start, { x: 5000, y: 5000 }, BOUNDS)).toEqual({ x: 400, y: 250 })
    expect(dragPhotoOffset(start, { x: -5000, y: -5000 }, BOUNDS)).toEqual(PHOTO_ORIGIN)
  })

  it('is reversible — dragging back lands on the original spot', () => {
    const moved = dragPhotoOffset(start, { x: 420, y: 300 }, BOUNDS)
    const back = dragPhotoOffset(
      { pointerX: 420, pointerY: 300, offsetX: moved.x, offsetY: moved.y },
      { x: 300, y: 200 },
      BOUNDS
    )
    expect(back).toEqual({ x: 100, y: 50 })
  })
})

describe('reflowPhotoOffset', () => {
  it('pulls the photo back in when the box shrinks under it', () => {
    const parked = { x: 400, y: 250 }
    const shrunk = { boxW: 300, boxH: 220, photoW: 200, photoH: 150 }
    expect(reflowPhotoOffset(parked, shrunk)).toEqual({ x: 100, y: 70 })
  })

  it('leaves the photo alone when it still fits', () => {
    expect(reflowPhotoOffset({ x: 120, y: 80 }, BOUNDS)).toEqual({ x: 120, y: 80 })
  })
})
