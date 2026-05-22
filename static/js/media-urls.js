/**
 * URL полного изображения и облегчённых превью (WebP thumb / preview).
 */

const MOBILE_PREVIEW_MQ = window.matchMedia('(max-width: 768px)');

function resolveMediaUrl(url) {
  if (!url) return url;
  const s = String(url).trim();
  if (s.startsWith('/media/')) {
    return `${window.location.origin}${s}`;
  }
  try {
    const u = new URL(s, window.location.origin);
    if (u.pathname.startsWith('/media/')) {
      return `${window.location.origin}${u.pathname}${u.search}`;
    }
  } catch {
    /* ignore */
  }
  return s;
}

function isMobileViewport() {
  return MOBILE_PREVIEW_MQ.matches;
}

function _pathname(url) {
  try {
    return new URL(url, window.location.origin).pathname;
  } catch {
    return '';
  }
}

/**
 * Полный URL (lightbox, скачивание, прикрепление в composer).
 */
function mediaFullUrl(url) {
  const resolved = resolveMediaUrl(url);
  if (!resolved) return resolved;
  const path = _pathname(resolved);
  const asset = path.match(/^\/media\/asset\/([0-9a-f-]{36})(?:\/(?:thumb|preview|llm))?$/i);
  if (asset) {
    return `${window.location.origin}/media/asset/${asset[1]}`;
  }
  const genThumb = path.match(/^\/media\/generated\/thumbs\/([^/]+)$/i);
  if (genThumb) {
    return `${window.location.origin}/media/generated/${genThumb[1].replace(/\.(webp|jpe?g)$/i, '.png')}`;
  }
  return resolved;
}

/**
 * WebP-превью: /thumb на десктопе, /preview на узком экране.
 */
function mediaPreviewUrl(url) {
  const resolved = resolveMediaUrl(url);
  if (!resolved) return resolved;
  const path = _pathname(resolved);
  const variant = isMobileViewport() ? 'preview' : 'thumb';

  const asset = path.match(/^\/media\/asset\/([0-9a-f-]{36})(?:\/(?:thumb|preview|llm))?$/i);
  if (asset) {
    return `${window.location.origin}/media/asset/${asset[1]}/${variant}`;
  }

  const gen = path.match(/^\/media\/generated\/([^/]+)$/i);
  if (gen) {
    const stem = gen[1].replace(/\.[^.]+$/i, '');
    return `${window.location.origin}/media/generated/thumbs/${stem}.webp`;
  }

  const genThumb = path.match(/^\/media\/generated\/thumbs\/([^/]+)$/i);
  if (genThumb) {
    const name = genThumb[1];
    if (name.toLowerCase().endsWith('.jpg')) {
      return `${window.location.origin}/media/generated/thumbs/${name.replace(/\.jpe?g$/i, '.webp')}`;
    }
    return resolved;
  }

  if (path.endsWith('/thumb') || path.endsWith('/preview')) {
    return resolved;
  }

  return resolved;
}
