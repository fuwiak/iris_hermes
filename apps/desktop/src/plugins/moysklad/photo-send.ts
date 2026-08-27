/**
 * Build Telegram photo fields for mark-sent / mass-send.
 *
 * Card URLs may be protocol-relative (`//cdn…`) — browsers load them in <img>,
 * but the backend only treats http(s) as a photo attachment. data: URLs must
 * ride as image_base64, never as image_url.
 *
 * Send shape: text message first, then each tray photo as its own follow-up
 * message (empty caption). A Telegram caption holds one picture and 1024 chars.
 */

export interface SendImageLike {
  id?: string
  name?: string
  dataUrl?: string
  url?: string
}

/** A composer attachment that is ready for the tray — `id` + `name` always set. */
export interface ComposerImage {
  id: string
  name: string
  dataUrl?: string
  url?: string
}

export interface ImageSendFields {
  image_url: string
  image_base64: string
  image_name: string
}

/** Stable React key / remove handle — never reuse across adds. */
export function newPhotoId(): string {
  return `p_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`
}

/** Same bytes / same CDN url → same picture (for dedupe). */
export function photoContentKey(photo: SendImageLike): string {
  const data = String(photo.dataUrl || '').trim()
  const url = normalizeRemoteImageUrl(photo.url || '')
  return `${data}|${url}`
}

export function photoKey(photo: ComposerImage | SendImageLike): string {
  if (photo.id) {
    return photo.id
  }
  return photoContentKey(photo) || String(photo.name || 'photo')
}

/**
 * Append a photo to the tray. Same CDN/bytes is ignored; a new file always
 * gets a fresh id so «+» never silently no-ops.
 */
export function addPhotoToTray(
  tray: ComposerImage[],
  photo: SendImageLike & { name: string }
): ComposerImage[] {
  const content = photoContentKey(photo)
  if (content !== '|' && tray.some(item => photoContentKey(item) === content)) {
    return tray
  }
  const next: ComposerImage = {
    id: photo.id || newPhotoId(),
    name: (photo.name || 'photo.jpg').trim() || 'photo.jpg',
    dataUrl: photo.dataUrl,
    url: photo.url
  }
  return [...tray, next]
}

/** `//host/path` → `https://host/path`; trim; leave data:/http alone. */
export function normalizeRemoteImageUrl(raw: string): string {
  const url = String(raw || '').trim()
  if (!url) {
    return ''
  }
  if (url.startsWith('//')) {
    return `https:${url}`
  }
  return url
}

export function isHttpImageUrl(url: string): boolean {
  const u = normalizeRemoteImageUrl(url)
  return u.startsWith('http://') || u.startsWith('https://')
}

export function isDataImageUrl(url: string): boolean {
  return String(url || '')
    .trim()
    .startsWith('data:')
}

/**
 * Split a composer attachment into API fields Telegram can actually send.
 * Bytes first — the backend host often cannot reach marketplace CDNs
 * (Errno 101). A leftover URL is last-resort for Bot API (Telegram fetches).
 */
export function buildImageSendFields(image: SendImageLike | null | undefined): ImageSendFields {
  const image_name = (image?.name || 'photo.jpg').trim() || 'photo.jpg'
  if (!image) {
    return { image_url: '', image_base64: '', image_name }
  }

  const dataUrl = String(image.dataUrl || '').trim()
  const url = normalizeRemoteImageUrl(image.url || '')

  if (isDataImageUrl(dataUrl)) {
    return { image_url: '', image_base64: dataUrl, image_name }
  }
  if (isDataImageUrl(url)) {
    return { image_url: '', image_base64: url, image_name }
  }
  if (dataUrl) {
    return { image_url: '', image_base64: dataUrl, image_name }
  }
  if (isHttpImageUrl(url)) {
    return { image_url: url, image_base64: '', image_name }
  }
  return { image_url: url, image_base64: '', image_name }
}

/** Normalize a marketplace photo for composer state (url or dataUrl). */
export function cardPhotoAttachment(name: string, photoUrl: string): ComposerImage {
  const url = normalizeRemoteImageUrl(photoUrl)
  if (isDataImageUrl(url)) {
    return { id: newPhotoId(), name, dataUrl: url }
  }
  return { id: newPhotoId(), name, url }
}

/** Telegram rejects anything past 10 MB; stay under it before we upload. */
export const MAX_PHOTO_BYTES = 9 * 1024 * 1024

/**
 * Turn a remote card photo into bytes, in the client, before sending.
 *
 * The backend does not always have egress to the marketplace CDNs — a send
 * came back «Текст ушёл, фото 0/1: [Errno 101] Network is unreachable» — and
 * MTProto/Bot API cannot fetch what the server cannot reach either. The app
 * itself CAN: it renders these very thumbnails, and both flowwow and Yandex
 * answer with `access-control-allow-origin: *`. So download here and upload
 * the bytes.
 *
 * Best-effort: anything that goes wrong (CORS, offline, oversize) returns the
 * photo untouched, and the URL still rides to the backend as before.
 */
export async function rasterizeRemoteImage(
  url: string,
  deps: {
    ImageCtor?: typeof Image
    documentObj?: Pick<Document, 'createElement'>
  } = {}
): Promise<string> {
  const href = normalizeRemoteImageUrl(url)
  if (!isHttpImageUrl(href)) {
    return ''
  }

  const ImageCtor = deps.ImageCtor ?? (typeof Image === 'function' ? Image : undefined)
  const doc = deps.documentObj ?? (typeof document !== 'undefined' ? document : undefined)

  if (!ImageCtor || !doc) {
    return ''
  }

  return new Promise(resolve => {
    const img = new ImageCtor()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const canvas = doc.createElement('canvas') as HTMLCanvasElement
        canvas.width = img.naturalWidth || img.width
        canvas.height = img.naturalHeight || img.height
        if (!canvas.width || !canvas.height) {
          resolve('')
          return
        }
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          resolve('')
          return
        }
        ctx.drawImage(img, 0, 0)
        const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
        resolve(isDataImageUrl(dataUrl) ? dataUrl : '')
      } catch {
        resolve('')
      }
    }
    img.onerror = () => resolve('')
    img.src = href
  })
}

export async function resolvePhotoBytes(
  photo: SendImageLike,
  deps: {
    fetchImpl?: typeof fetch
    toDataUrl?: (blob: Blob) => Promise<string>
    rasterize?: (url: string) => Promise<string>
  } = {}
): Promise<SendImageLike> {
  const url = normalizeRemoteImageUrl(photo.url || '')

  if (isDataImageUrl(photo.dataUrl || '') || !isHttpImageUrl(url)) {
    return photo
  }

  const doFetch = deps.fetchImpl ?? (typeof fetch === 'function' ? fetch : undefined)

  if (doFetch) {
    try {
      const resp = await doFetch(url, { mode: 'cors', referrerPolicy: 'no-referrer' } as RequestInit)

      if (resp.ok) {
        const blob = await resp.blob()

        if (blob.size && blob.size <= MAX_PHOTO_BYTES) {
          const dataUrl = await (deps.toDataUrl ?? blobToDataUrl)(blob)

          if (isDataImageUrl(dataUrl)) {
            return { ...photo, dataUrl }
          }
        }
      }
    } catch {
      // CORS on fetch() is common even when <img> already painted the card.
    }
  }

  const rasterize = deps.rasterize ?? ((href: string) => rasterizeRemoteImage(href))
  try {
    const painted = await rasterize(url)
    if (isDataImageUrl(painted)) {
      return { ...photo, dataUrl: painted }
    }
  } catch {
    // tainted canvas / no DOM
  }

  return photo
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onerror = () => reject(reader.error ?? new Error('read failed'))
    reader.onload = () => resolve(String(reader.result || ''))
    reader.readAsDataURL(blob)
  })
}

/** Resolve a whole tray, keeping order; failures fall back to their URL. */
export function resolveTrayBytes(
  photos: readonly SendImageLike[],
  deps?: Parameters<typeof resolvePhotoBytes>[1]
): Promise<SendImageLike[]> {
  return Promise.all(photos.map(photo => resolvePhotoBytes(photo, deps)))
}
