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

/**
 * Цель для галереи/удаления: asset в БД или файл на диске.
 * @returns {{ source: 'db'|'disk', id: string, filename: string, url: string } | null}
 */
function parseMediaGalleryTarget(url) {
  const full = mediaFullUrl(url);
  if (!full) return null;
  const path = _pathname(full);
  const asset = path.match(/^\/media\/asset\/([0-9a-f-]{36})$/i);
  if (asset) {
    return {
      source: 'db',
      id: asset[1],
      filename: `asset-${asset[1].slice(0, 8)}.png`,
      url: full,
    };
  }
  const gen = path.match(/^\/media\/generated\/([^/]+)$/i);
  if (gen && !path.includes('/thumbs/')) {
    return {
      source: 'disk',
      id: gen[1],
      filename: gen[1],
      url: full,
    };
  }
  return null;
}

/**
 * Скачивание медиа. Для same-origin /media/* — прямая ссылка без blob
 * (на HTTP не появляется предупреждение Chrome про insecure blob).
 */
async function downloadMediaFile(url, filename = '') {
  const full = mediaFullUrl(url);
  if (!full) throw new Error('Некорректный URL');

  let name = filename;
  if (!name) {
    try {
      const path = new URL(full, window.location.href).pathname;
      name = path.split('/').filter(Boolean).pop() || 'image.png';
    } catch {
      name = 'image.png';
    }
  }

  try {
    const parsed = new URL(full, window.location.href);
    if (parsed.origin === window.location.origin && parsed.pathname.startsWith('/media/')) {
      const a = document.createElement('a');
      a.href = parsed.href;
      a.download = name;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      return;
    }
  } catch {
    /* fetch + blob */
  }

  const res = await fetch(full, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('Не удалось загрузить файл');
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
