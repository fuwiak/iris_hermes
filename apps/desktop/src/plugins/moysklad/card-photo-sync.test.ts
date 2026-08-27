import { describe, expect, it } from 'vitest'

import {
  cardNamesInText,
  draftKeepsPhoto,
  normalizeCardName,
  photoForDraftText
} from './card-photo-sync'

const CARDS = [
  {
    name: '❣️️️ Букет для любимой из красных французских роз в стильной упаковке',
    image: 'https://content2.flowwow-images.com/red.jpg'
  },
  {
    name: 'Авторский букет с  персиковыми ранункулюсами, размер s',
    image: 'https://content2.flowwow-images.com/peach.jpg'
  },
  { name: 'Букет без фото', image: '' }
]

describe('cardNamesInText', () => {
  it('pulls quoted card titles in order', () => {
    expect(cardNamesInText('привет\n\n«А»\nЦена: 1 ₽\n\n«Б»\nЦена: 2 ₽')).toEqual(['А', 'Б'])
  })

  it('ignores text with no card blocks', () => {
    expect(cardNamesInText('Ханс, привет! Заказов пока не было.')).toEqual([])
  })
})

describe('normalizeCardName', () => {
  it('folds emoji, punctuation and double spaces like the backend', () => {
    expect(normalizeCardName('❣️️️ Букет,  размер S!')).toBe('букет размер s')
  })
})

describe('photoForDraftText', () => {
  it('recovers the photo for a draft restored as text only', () => {
    const draft =
      'Ханс, привет!\n\n«❣️️️ Букет для любимой из красных французских роз в стильной упаковке»\nЦена: 5 990 ₽'

    expect(photoForDraftText(draft, CARDS)).toEqual({
      name: '❣️️️ Букет для любимой из красных французских роз в стильной упаковке',
      url: 'https://content2.flowwow-images.com/red.jpg'
    })
  })

  it('lets the last quoted card win, like addCardToMessage', () => {
    const draft = '«❣️️️ Букет для любимой из красных французских роз в стильной упаковке»\n\n«Авторский букет с персиковыми ранункулюсами, размер S»'

    expect(photoForDraftText(draft, CARDS)?.url).toBe(
      'https://content2.flowwow-images.com/peach.jpg'
    )
  })

  it('skips a trailing card that has no image and falls back to an earlier one', () => {
    const draft = '«Авторский букет с персиковыми ранункулюсами, размер S»\n\n«Букет без фото»'

    expect(photoForDraftText(draft, CARDS)?.url).toBe(
      'https://content2.flowwow-images.com/peach.jpg'
    )
  })

  it('returns null for prose or an unknown card', () => {
    expect(photoForDraftText('просто текст', CARDS)).toBeNull()
    expect(photoForDraftText('«Неизвестный букет»', CARDS)).toBeNull()
    expect(photoForDraftText('«Букет без фото»', CARDS)).toBeNull()
  })
})

describe('draftKeepsPhoto', () => {
  it('drops a card photo once its block is gone from the text', () => {
    const photo = { name: 'Авторский букет с  персиковыми ранункулюсами, размер s', url: 'https://content2.flowwow-images.com/peach.jpg' }

    expect(draftKeepsPhoto('«Авторский букет с персиковыми ранункулюсами, размер S»', photo, CARDS)).toBe(true)
    expect(draftKeepsPhoto('Ханс, привет!', photo, CARDS)).toBe(false)
  })

  it('never drops an uploaded or hand-picked picture', () => {
    expect(draftKeepsPhoto('что угодно', { name: 'p.jpg', dataUrl: 'data:image/png;base64,aa' }, CARDS)).toBe(true)
    expect(draftKeepsPhoto('что угодно', { name: 'p.jpg', url: 'https://cdn/other.jpg' }, CARDS)).toBe(true)
  })

  it('is false when there is no photo at all', () => {
    expect(draftKeepsPhoto('«Букет»', null, CARDS)).toBe(false)
  })
})
