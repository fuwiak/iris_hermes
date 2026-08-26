import { describe, expect, it } from 'vitest'

import {
  buildImageSendFields,
  cardPhotoAttachment,
  isHttpImageUrl,
  normalizeRemoteImageUrl
} from './photo-send'

describe('normalizeRemoteImageUrl', () => {
  it('upgrades protocol-relative CDN urls', () => {
    expect(normalizeRemoteImageUrl('//avatars.mds.yandex.net/get-mpic/x')).toBe(
      'https://avatars.mds.yandex.net/get-mpic/x'
    )
  })

  it('leaves http(s) and data alone', () => {
    expect(normalizeRemoteImageUrl('https://a/b.jpg')).toBe('https://a/b.jpg')
    expect(normalizeRemoteImageUrl('data:image/png;base64,aa')).toBe('data:image/png;base64,aa')
  })
})

describe('buildImageSendFields', () => {
  it('sends https card urls as image_url (not base64)', () => {
    expect(
      buildImageSendFields({ name: 'Букет', url: 'https://content2.flowwow-images.com/x.jpg' })
    ).toEqual({
      image_url: 'https://content2.flowwow-images.com/x.jpg',
      image_base64: '',
      image_name: 'Букет'
    })
  })

  it('upgrades // urls so backend photo gate accepts them', () => {
    const fields = buildImageSendFields({
      name: 'Открытка',
      url: '//avatars.mds.yandex.net/get-mpic/x'
    })
    expect(fields.image_url).toBe('https://avatars.mds.yandex.net/get-mpic/x')
    expect(fields.image_base64).toBe('')
    expect(isHttpImageUrl(fields.image_url)).toBe(true)
  })

  it('puts data: on image_base64 even when stored in url', () => {
    const data = 'data:image/jpeg;base64,/9j/4AAQ'
    expect(buildImageSendFields({ name: 'p.jpg', url: data })).toEqual({
      image_url: '',
      image_base64: data,
      image_name: 'p.jpg'
    })
    expect(buildImageSendFields({ name: 'p.jpg', dataUrl: data, url: 'https://ignore' })).toEqual({
      image_url: '',
      image_base64: data,
      image_name: 'p.jpg'
    })
  })

  it('does not blank base64 just because url is truthy relative junk', () => {
    // Old bug: imageUrl ? '' : dataUrl wiped uploads when url was set to garbage.
    expect(
      buildImageSendFields({
        name: 'p.jpg',
        url: 'p1.jpg',
        dataUrl: 'data:image/png;base64,aa'
      })
    ).toEqual({
      image_url: '',
      image_base64: 'data:image/png;base64,aa',
      image_name: 'p.jpg'
    })
  })
})

describe('cardPhotoAttachment', () => {
  it('normalizes marketplace urls for composer state', () => {
    expect(cardPhotoAttachment('A', '//cdn/x.jpg')).toEqual({
      name: 'A',
      url: 'https://cdn/x.jpg'
    })
  })
})
