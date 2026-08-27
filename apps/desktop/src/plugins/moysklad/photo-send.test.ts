import { describe, expect, it } from 'vitest'

import {
  addPhotoToTray,
  buildImageSendFields,
  cardPhotoAttachment,
  isHttpImageUrl,
  MAX_PHOTO_BYTES,
  normalizeRemoteImageUrl,
  photoKey,
  resolvePhotoBytes,
  resolveTrayBytes
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
    const out = cardPhotoAttachment('A', '//cdn/x.jpg')
    expect(out.name).toBe('A')
    expect(out.url).toBe('https://cdn/x.jpg')
    expect(out.id).toMatch(/^p_/)
  })
})

describe('addPhotoToTray', () => {
  it('dedupes same CDN url but assigns a fresh id per new picture', () => {
    const first = addPhotoToTray([], { name: 'a', url: 'https://cdn/a.jpg' })
    const again = addPhotoToTray(first, { name: 'a', url: 'https://cdn/a.jpg' })
    expect(again).toHaveLength(1)
    const second = addPhotoToTray(first, { name: 'b', url: 'https://cdn/b.jpg' })
    expect(second).toHaveLength(2)
    expect(photoKey(second[0]!)).not.toBe(photoKey(second[1]!))
  })
})

describe('resolvePhotoBytes', () => {
  const blob = (size: number) => ({ size, type: 'image/jpeg' }) as Blob
  const toDataUrl = async () => 'data:image/jpeg;base64,AAAA'

  it('uploads bytes fetched in the client, so a CDN-less backend still sends', async () => {
    const fetchImpl = (async () => ({ ok: true, blob: async () => blob(1234) })) as unknown as typeof fetch

    expect(
      await resolvePhotoBytes({ name: 'Букет', url: '//cdn/x.jpg' }, { fetchImpl, toDataUrl })
    ).toEqual({ name: 'Букет', url: '//cdn/x.jpg', dataUrl: 'data:image/jpeg;base64,AAAA' })
  })

  it('rasterizes via canvas when fetch is CORS-blocked — same path as the live send', async () => {
    const boom = (async () => {
      throw new Error('CORS')
    }) as unknown as typeof fetch
    const photo = { name: 'Букет', url: 'https://cdn/x.jpg' }

    expect(
      await resolvePhotoBytes(photo, {
        fetchImpl: boom,
        rasterize: async () => 'data:image/jpeg;base64,FROMIMG'
      })
    ).toEqual({ ...photo, dataUrl: 'data:image/jpeg;base64,FROMIMG' })
  })

  it('keeps the url only when fetch AND rasterize both fail', async () => {
    const boom = (async () => {
      throw new Error('CORS')
    }) as unknown as typeof fetch
    const photo = { name: 'Букет', url: 'https://cdn/x.jpg' }

    expect(
      await resolvePhotoBytes(photo, { fetchImpl: boom, rasterize: async () => '' })
    ).toEqual(photo)

    const notOk = (async () => ({ ok: false, blob: async () => blob(1) })) as unknown as typeof fetch

    expect(
      await resolvePhotoBytes(photo, { fetchImpl: notOk, rasterize: async () => '' })
    ).toEqual(photo)
  })

  it('refuses a blob Telegram would reject anyway', async () => {
    const huge = (async () => ({
      ok: true,
      blob: async () => blob(MAX_PHOTO_BYTES + 1)
    })) as unknown as typeof fetch
    const photo = { name: 'Букет', url: 'https://cdn/x.jpg' }

    expect(
      await resolvePhotoBytes(photo, { fetchImpl: huge, toDataUrl, rasterize: async () => '' })
    ).toEqual(photo)
  })

  it('leaves uploads and non-http attachments alone', async () => {
    const fetchImpl = (async () => {
      throw new Error('must not be called')
    }) as unknown as typeof fetch
    const upload = { name: 'p.jpg', dataUrl: 'data:image/png;base64,aa' }

    expect(await resolvePhotoBytes(upload, { fetchImpl, toDataUrl })).toEqual(upload)
    expect(await resolvePhotoBytes({ name: 'p.jpg', url: 'p1.jpg' }, { fetchImpl, toDataUrl })).toEqual({
      name: 'p.jpg',
      url: 'p1.jpg'
    })
  })
})

describe('resolveTrayBytes', () => {
  it('keeps tray order and mixes resolved with fallback photos', async () => {
    const fetchImpl = (async (input: string) =>
      input.includes('bad')
        ? { ok: false, blob: async () => ({ size: 1 }) as Blob }
        : { ok: true, blob: async () => ({ size: 10, type: 'image/jpeg' }) as Blob }) as unknown as typeof fetch

    const out = await resolveTrayBytes(
      [
        { name: 'A', url: 'https://cdn/a.jpg' },
        { name: 'B', url: 'https://cdn/bad.jpg' }
      ],
      {
        fetchImpl,
        toDataUrl: async () => 'data:image/jpeg;base64,AAAA',
        rasterize: async () => ''
      }
    )

    expect(out.map(p => p.name)).toEqual(['A', 'B'])
    expect(out[0].dataUrl).toBe('data:image/jpeg;base64,AAAA')
    expect(out[1].dataUrl).toBeUndefined()
  })
})
