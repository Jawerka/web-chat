/**
 * One-click open in SD WebUI via sd-webui-web-chat-bridge extension.
 */
(function () {
  'use strict';

  const SD_URL_KEY = 'webchat_sd_webui_url';
  let cachedConfigUrl = '';

  function normalizeBaseUrl(url) {
    return String(url || '').trim().replace(/\/+$/, '');
  }

  function readStoredSdUrl() {
    try {
      return normalizeBaseUrl(localStorage.getItem(SD_URL_KEY) || '');
    } catch (_err) {
      return '';
    }
  }

  async function ensureConfigSdUrl() {
    if (cachedConfigUrl) return cachedConfigUrl;
    try {
      const res = await fetch('/api/config', { credentials: 'same-origin' });
      if (!res.ok) return '';
      const data = await res.json();
      cachedConfigUrl = normalizeBaseUrl(data.sd_webui_url || '');
      return cachedConfigUrl;
    } catch (_err) {
      return '';
    }
  }

  async function resolveSdWebuiUrl(override) {
    const explicit = normalizeBaseUrl(override);
    if (explicit) return explicit;
    const stored = readStoredSdUrl();
    if (stored) return stored;
    return ensureConfigSdUrl();
  }

  function itemHasSdMetadata(item) {
    if (!item) return false;
    if (typeof item.has_metadata === 'boolean') return item.has_metadata;
    return Boolean(item.sd_prompt || item.sd_negative || item.sd_params);
  }

  function itemFromMediaUrl(url) {
    if (typeof parseMediaGalleryTarget !== 'function') return null;
    const target = parseMediaGalleryTarget(url);
    if (!target) return null;
    const name = target.filename || target.id || '';
    const isPng = /\.png$/i.test(name);
    return {
      id: target.id,
      source: target.source,
      url: target.url,
      filename: target.filename,
      has_metadata: target.source === 'db' || isPng,
    };
  }

  function syncSdOpenButton(btn, item) {
    if (!btn) return;
    const enabled = itemHasSdMetadata(item);
    btn.disabled = !enabled;
    btn.classList.toggle('is-disabled', !enabled);
    btn.title = enabled
      ? 'Отправить в SD WebUI (img2img)'
      : 'Нет SD-метаданных для импорта';
    btn.setAttribute('aria-label', btn.title);
  }

  async function openGalleryItemInSd(item, options) {
    const opts = options || {};
    if (!item) throw new Error('Изображение не выбрано');
    if (!itemHasSdMetadata(item)) throw new Error('Нет SD-метаданных');

    const sdWebuiUrl = await resolveSdWebuiUrl(opts.sdWebuiUrl);
    if (!sdWebuiUrl) throw new Error('SD WebUI URL не настроен');

    const source = item.source === 'disk' ? 'disk' : 'db';
    const res = await fetch('/api/sd-bridge/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        asset_id: item.id,
        source,
        sd_webui_url: sdWebuiUrl,
      }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail || res.statusText || 'Import failed');
    }

    if (!body.queued) {
      throw new Error('SD WebUI не принял импорт');
    }
    const msg = 'Отправлено в SD — откройте img2img когда будете готовы';
    if (window.WebChatToast) {
      window.WebChatToast.show(msg, 'success', 4000);
    }
    return body;
  }

  async function openMediaUrlInSd(url, options) {
    const item = itemFromMediaUrl(url);
    if (!item) throw new Error('Изображение недоступно для SD WebUI');
    return openGalleryItemInSd(item, options);
  }

  window.GallerySdBridge = {
    itemFromMediaUrl,
    itemHasSdMetadata,
    syncSdOpenButton,
    openGalleryItemInSd,
    openMediaUrlInSd,
    resolveSdWebuiUrl,
  };
})();
