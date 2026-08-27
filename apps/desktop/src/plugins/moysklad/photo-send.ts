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

export interface PhotoResolveStep {
  name: string
  url: string
  via: 'already' | 'fetch' | 'electron' | 'canvas' | 'none'
  byteLength: number
  error?: string
}

export interface ResolvePhotoDeps {
  fetchImpl?: typeof fetch
  toDataUrl?: (blob: Blob) => Promise<string>
  rasterize?: (url: string) => Promise<string>
  /** Main-process download (no CORS). Desktop only. */
  electronFetch?: (url: string) => Promise<string>
  onStep?: (step: PhotoResolveStep) => void
  fetchTimeoutMs?: number
  canvasTimeoutMs?: number
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
 * (Errno 101). Keep the http(s) URL alongside bytes so Bot API can still
 * fetch the CDN if multipart upload from Selectel dies.
 */
export function buildImageSendFields(image: SendImageLike | null | undefined): ImageSendFields {
  const image_name = (image?.name || 'photo.jpg').trim() || 'photo.jpg'
  if (!image) {
    return { image_url: '', image_base64: '', image_name }
  }

  const dataUrl = String(image.dataUrl || '').trim()
  const url = normalizeRemoteImageUrl(image.url || '')
  const httpUrl = isHttpImageUrl(url) ? url : ''

  if (isDataImageUrl(dataUrl)) {
    return { image_url: httpUrl, image_base64: dataUrl, image_name }
  }
  if (isDataImageUrl(url)) {
    return { image_url: '', image_base64: url, image_name }
  }
  if (dataUrl) {
    return { image_url: httpUrl, image_base64: dataUrl, image_name }
  }
  if (httpUrl) {
    return { image_url: httpUrl, image_base64: '', image_name }
  }
  return { image_url: url, image_base64: '', image_name }
}

/** Compact tray line for the status bar / breadcrumb log. No payload. */
export function describeImageFields(fields: ImageSendFields[]): string {
  if (!fields.length) {
    return 'tray=0'
  }
  return fields
    .map((item, idx) => {
      const raw = (item.image_base64 || '').replace(/^data:[^,]*,/, '')
      const bytes = raw ? Math.floor((raw.length * 3) / 4) : 0
      const url = (item.image_url || '').slice(0, 48)
      return `#${idx + 1} ${item.image_name} bytes=${bytes}${url ? ` url=${url}` : ''}`
    })
    .join('; ')
}

export function fieldsMissingBytes(fields: ImageSendFields[]): ImageSendFields[] {
  return fields.filter(item => !String(item.image_base64 || '').trim())
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
export const PHOTO_FETCH_TIMEOUT_MS = 12_000
export const PHOTO_CANVAS_TIMEOUT_MS = 8_000

function dataUrlByteLength(dataUrl: string): number {
  const raw = String(dataUrl || '')
    .trim()
    .replace(/^data:[^,]*,/, '')
  return raw ? Math.floor((raw.length * 3) / 4) : 0
}

async function withTimeout<T>(work: Promise<T>, ms: number, fallback: T): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      work,
      new Promise<T>(resolve => {
        timer = setTimeout(() => resolve(fallback), ms)
      })
    ])
  } finally {
    if (timer) {
      clearTimeout(timer)
    }
  }
}

/**
 * Turn a remote card photo into bytes, in the client, before sending.
 *
 * The backend does not always have egress to the marketplace CDNs — a send
 * came back «Текст ушёл, фото 0/1: [Errno 101] Network is unreachable» — and
 * MTProto/Bot API cannot fetch what the server cannot reach either. The app
 * itself CAN: it renders these very thumbnails. Download here and upload bytes.
 *
 * Timeouts are mandatory: a hung fetch used to freeze «Отправка через Telegram…»
 * forever after the text had already left.
 */
export async function rasterizeRemoteImage(
  url: string,
  deps: {
    ImageCtor?: typeof Image
    documentObj?: Pick<Document, 'createElement'>
    timeoutMs?: number
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

  const timeoutMs = deps.timeoutMs ?? PHOTO_CANVAS_TIMEOUT_MS

  return new Promise(resolve => {
    let settled = false
    const finish = (value: string) => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      resolve(value)
    }
    const timer = setTimeout(() => finish(''), timeoutMs)
    const img = new ImageCtor()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const canvas = doc.createElement('canvas') as HTMLCanvasElement
        canvas.width = img.naturalWidth || img.width
        canvas.height = img.naturalHeight || img.height
        if (!canvas.width || !canvas.height) {
          finish('')
          return
        }
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          finish('')
          return
        }
        ctx.drawImage(img, 0, 0)
        const dataUrl = canvas.toDataURL('image/jpeg', 0.92)
        finish(isDataImageUrl(dataUrl) ? dataUrl : '')
      } catch {
        finish('')
      }
    }
    img.onerror = () => finish('')
    img.src = href
  })
}

function defaultElectronFetch(): ((url: string) => Promise<string>) | undefined {
  if (typeof window === 'undefined') {
    return undefined
  }
  const fn = window.hermesDesktop?.fetchUrlAsDataUrl
  return typeof fn === 'function' ? (href: string) => fn.call(window.hermesDesktop, href) : undefined
}

export async function resolvePhotoBytes(
  photo: SendImageLike,
  deps: ResolvePhotoDeps = {}
): Promise<SendImageLike> {
  const url = normalizeRemoteImageUrl(photo.url || '')
  const emit = (step: PhotoResolveStep) => {
    try {
      deps.onStep?.(step)
    } catch {
      // logging must not break a send
    }
  }

  if (isDataImageUrl(photo.dataUrl || '') || !isHttpImageUrl(url)) {
    emit({
      name: photo.name || '',
      url,
      via: isDataImageUrl(photo.dataUrl || '') ? 'already' : 'none',
      byteLength: dataUrlByteLength(photo.dataUrl || '')
    })
    return photo
  }

  const doFetch = deps.fetchImpl ?? (typeof fetch === 'function' ? fetch : undefined)
  const fetchTimeout = deps.fetchTimeoutMs ?? PHOTO_FETCH_TIMEOUT_MS

  if (doFetch) {
    try {
      const controller = typeof AbortController === 'function' ? new AbortController() : undefined
      const abortTimer = controller ? setTimeout(() => controller.abort(), fetchTimeout) : undefined
      const resp = await withTimeout(
        doFetch(url, {
          mode: 'cors',
          referrerPolicy: 'no-referrer',
          signal: controller?.signal
        } as RequestInit),
        fetchTimeout,
        null as unknown as Response
      )
      if (abortTimer) {
        clearTimeout(abortTimer)
      }

      if (resp && resp.ok) {
        const blob = await resp.blob()

        if (blob.size && blob.size <= MAX_PHOTO_BYTES) {
          const dataUrl = await (deps.toDataUrl ?? blobToDataUrl)(blob)

          if (isDataImageUrl(dataUrl)) {
            emit({
              name: photo.name || '',
              url,
              via: 'fetch',
              byteLength: blob.size
            })
            return { ...photo, dataUrl }
          }
        }
      }
    } catch (err) {
      emit({
        name: photo.name || '',
        url,
        via: 'none',
        byteLength: 0,
        error: `fetch:${err instanceof Error ? err.message : String(err)}`
      })
    }
  }

  const electronFetch = deps.electronFetch ?? defaultElectronFetch()
  if (electronFetch) {
    try {
      const painted = await withTimeout(electronFetch(url), fetchTimeout, '')
      if (isDataImageUrl(painted) && dataUrlByteLength(painted) <= MAX_PHOTO_BYTES) {
        emit({
          name: photo.name || '',
          url,
          via: 'electron',
          byteLength: dataUrlByteLength(painted)
        })
        return { ...photo, dataUrl: painted }
      }
    } catch (err) {
      emit({
        name: photo.name || '',
        url,
        via: 'none',
        byteLength: 0,
        error: `electron:${err instanceof Error ? err.message : String(err)}`
      })
    }
  }

  const rasterize =
    deps.rasterize ?? ((href: string) => rasterizeRemoteImage(href, { timeoutMs: deps.canvasTimeoutMs }))
  try {
    const painted = await rasterize(url)
    if (isDataImageUrl(painted)) {
      emit({
        name: photo.name || '',
        url,
        via: 'canvas',
        byteLength: dataUrlByteLength(painted)
      })
      return { ...photo, dataUrl: painted }
    }
  } catch (err) {
    emit({
      name: photo.name || '',
      url,
      via: 'none',
      byteLength: 0,
      error: `canvas:${err instanceof Error ? err.message : String(err)}`
    })
  }

  emit({ name: photo.name || '', url, via: 'none', byteLength: 0, error: 'no_bytes' })
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
  deps?: ResolvePhotoDeps
): Promise<SendImageLike[]> {
  return Promise.all(photos.map(photo => resolvePhotoBytes(photo, deps)))
}
