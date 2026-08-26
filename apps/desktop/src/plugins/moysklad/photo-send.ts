/**
 * Build Telegram photo fields for mark-sent / mass-send.
 *
 * Card URLs may be protocol-relative (`//cdn…`) — browsers load them in <img>,
 * but the backend only treats http(s) as a photo attachment. data: URLs must
 * ride as image_base64, never as image_url.
 */

export interface SendImageLike {
  name?: string
  dataUrl?: string
  url?: string
}

export interface ImageSendFields {
  image_url: string
  image_base64: string
  image_name: string
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
 * Prefer http(s) URL (small JSON). data: → base64. Never put data: in image_url.
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
  if (isHttpImageUrl(url)) {
    return { image_url: url, image_base64: '', image_name }
  }
  // Relative / unknown — keep whatever we have; backend may fetch or reject.
  if (dataUrl) {
    return { image_url: '', image_base64: dataUrl, image_name }
  }
  return { image_url: url, image_base64: '', image_name }
}

/** Normalize a marketplace photo for composer state (url or dataUrl). */
export function cardPhotoAttachment(name: string, photoUrl: string): SendImageLike {
  const url = normalizeRemoteImageUrl(photoUrl)
  if (isDataImageUrl(url)) {
    return { name, dataUrl: url }
  }
  return { name, url }
}
