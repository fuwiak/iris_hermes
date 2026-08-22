/**
 * «Карточки» tab — marketplace product cards in one place.
 *
 * Reads GET /cards/marketplaces (moysklad plugin API): Flowwow is live,
 * Yandex Market shows a "needs token" placeholder until the Api-Key is
 * configured. First step of the card-autopublish flow (call 21.08.2026).
 * Styles reuse existing ms-* classes + local inline grid so this tab does
 * not touch moysklad.css.
 */

import { useCallback, useEffect, useState } from 'react'

type CardsRest = <T>(path: string, opts?: { method?: string; timeoutMs?: number }) => Promise<T>

type MarketplaceProduct = {
  product_id?: number
  offer_id?: string
  name?: string
  description_preview?: string
  price?: string
  discount?: string
  currency?: string
  is_active?: boolean
  is_archived?: boolean
  url?: string
  image?: string
  images_count?: number
  card_status?: string
  content_rating?: number | null
}

type FlowwowSection = {
  configured?: boolean
  note?: string
  error?: string
  shop?: { shop_id?: number; name?: string; address?: string }
  products?: MarketplaceProduct[]
  total?: number
}

type YandexSection = {
  configured?: boolean
  note?: string
  error?: string
  business?: { id?: number; name?: string }
  products?: MarketplaceProduct[]
  total?: number | null
}

type CardsPayload = {
  ok?: boolean
  flowwow?: FlowwowSection
  yandex?: YandexSection
  generated_at?: string
}

const GRID_STYLE: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
  gap: 12,
  marginTop: 12
}

const CARD_STYLE: React.CSSProperties = {
  border: '1px solid var(--hermes-border, rgba(128,128,128,.25))',
  borderRadius: 8,
  overflow: 'hidden',
  display: 'flex',
  flexDirection: 'column',
  minHeight: 240
}

const THUMB_STYLE: React.CSSProperties = {
  width: '100%',
  height: 140,
  objectFit: 'cover',
  display: 'block',
  background: 'rgba(128,128,128,.12)'
}

function priceLabel(p: MarketplaceProduct): string {
  if (!p.price) {
    return '—'
  }

  const base = `${Number(p.price).toLocaleString('ru-RU')} ${p.currency || 'RUB'}`
  const discount = Number(p.discount || 0)
  return discount > 0 ? `${base} · скидка ${discount}%` : base
}

function ProductCard({ product }: { product: MarketplaceProduct }) {
  return (
    <div style={CARD_STYLE}>
      {product.image ? (
        <img alt={product.name || ''} loading="lazy" src={product.image} style={THUMB_STYLE} />
      ) : (
        <div style={{ ...THUMB_STYLE, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <span className="ms-muted">нет фото</span>
        </div>
      )}
      <div style={{ padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
        <strong style={{ fontSize: 13, lineHeight: 1.3 }}>
          {product.url ? (
            <a href={product.url} rel="noreferrer" target="_blank">
              {product.name || '—'}
            </a>
          ) : (
            product.name || '—'
          )}
        </strong>
        <span>{priceLabel(product)}</span>
        <span className="ms-muted" style={{ fontSize: 12 }}>
          {product.is_archived ? 'в архиве' : product.is_active ? 'активна' : 'скрыта'}
          {product.images_count ? ` · фото: ${product.images_count}` : ''}
          {product.content_rating != null ? ` · контент: ${product.content_rating}/100` : ''}
        </span>
      </div>
    </div>
  )
}

function FlowwowBlock({ section }: { section?: FlowwowSection }) {
  if (!section) {
    return null
  }

  if (!section.configured) {
    return <p className="ms-muted">{section.note || 'Flowwow не настроен.'}</p>
  }

  if (section.error) {
    return <p className="ms-error">Flowwow: {section.error}</p>
  }

  const products = section.products || []
  return (
    <>
      <p className="ms-muted">
        Магазин «{section.shop?.name || '—'}» ({section.shop?.address || '—'}) · карточек всего:{' '}
        {section.total ?? products.length}
      </p>
      {products.length ? (
        <div style={GRID_STYLE}>
          {products.map(product => (
            <ProductCard key={String(product.product_id ?? product.name)} product={product} />
          ))}
        </div>
      ) : (
        <p className="ms-muted">Карточек нет.</p>
      )}
    </>
  )
}

function YandexBlock({ section }: { section?: YandexSection }) {
  if (!section) {
    return null
  }

  if (!section.configured) {
    return <p className="ms-muted">{section.note || 'Нет доступа — нужен API-токен.'}</p>
  }

  if (section.error) {
    return <p className="ms-error">Яндекс Маркет: {section.error}</p>
  }

  const products = section.products || []
  return (
    <>
      <p className="ms-muted">
        Бизнес «{section.business?.name || '—'}» · показано карточек: {products.length}
        {' · контент-рейтинг из кабинета (0–100)'}
      </p>
      {products.length ? (
        <div style={GRID_STYLE}>
          {products.map(product => (
            <ProductCard key={String(product.offer_id ?? product.name)} product={product} />
          ))}
        </div>
      ) : (
        <p className="ms-muted">Карточек нет.</p>
      )}
    </>
  )
}

export function CardsPage({ rest }: { rest: CardsRest }) {
  const [data, setData] = useState<CardsPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(
    async (force: boolean) => {
      setLoading(true)
      setError('')
      try {
        const payload = await rest<CardsPayload>(
          `/cards/marketplaces?limit=100${force ? '&force=true' : ''}`,
          { timeoutMs: 120_000 }
        )
        setData(payload)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setLoading(false)
      }
    },
    [rest]
  )

  useEffect(() => {
    void load(false)
  }, [load])

  return (
    <div className="ms-page ms-cards-page">
      <div className="ms-page-head">
        <h1>Карточки</h1>
        <button className="ms-btn" disabled={loading} onClick={() => void load(true)} type="button">
          {loading ? 'Обновляем…' : 'Обновить'}
        </button>
      </div>
      <p className="ms-muted">
        Карточки товаров на маркетплейсах. Дальше здесь появится создание карточки из букета
        МоегоСклада и автопубликация на площадки.
      </p>
      {error ? <p className="ms-error">{error}</p> : null}
      <section className="ms-card-section">
        <h2>Flowwow</h2>
        {loading && !data ? <p className="ms-muted">Загружаем…</p> : <FlowwowBlock section={data?.flowwow} />}
      </section>
      <section className="ms-card-section">
        <h2>Яндекс Маркет</h2>
        {loading && !data ? <p className="ms-muted">Загружаем…</p> : <YandexBlock section={data?.yandex} />}
      </section>
    </div>
  )
}
