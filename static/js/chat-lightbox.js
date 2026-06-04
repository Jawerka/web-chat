/**
 * Lightbox чата: навигация, download, attach, focus trap (P5.4 / P5.9).
 * Подключается после lightbox-viewer.js; API: WebChatLightbox.*
 */
(function () {
  'use strict';

  function viewerCtx(app) {
    return {
      root: app.$.lightbox,
      img: app.$.lightboxImg,
      loader: app.$.lightboxLoader,
      prev: app.$.lightboxPrev,
      next: app.$.lightboxNext,
      counter: app.$.lightboxCounter,
      get index() {
        return app._lightboxIndex;
      },
      set index(v) {
        app._lightboxIndex = v;
      },
      getCount: () => app._lightboxUrls.length,
      getUrl: (i) => app._lightboxUrls[i],
      onBeforeShow: () => {
        if (app.$.lightbox.classList.contains('hidden')) {
          app._lightboxReturnFocus = document.activeElement;
          app.$.app?.setAttribute('aria-hidden', 'true');
        }
      },
      onShow: () => {
        app.$.lightboxImg.alt = `Изображение ${app._lightboxIndex + 1} из ${app._lightboxUrls.length}`;
        updateActions(app);
        document.getElementById('lightbox-close')?.focus({ preventScroll: true });
      },
    };
  }

  function isOpen(app) {
    return LightboxViewer.isOpen(viewerCtx(app));
  }

  function currentUrl(app) {
    return app._lightboxUrls[app._lightboxIndex] || app.$.lightboxImg?.src || '';
  }

  function filenameFromUrl(url) {
    try {
      const u = new URL(url, window.location.origin);
      const base = u.pathname.split('/').pop() || '';
      if (base && /\.[a-z0-9]+$/i.test(base)) return base;
      const assetMatch = u.pathname.match(/\/media\/asset\/([0-9a-f-]{36})/i);
      if (assetMatch) return `asset-${assetMatch[1].slice(0, 8)}.png`;
    } catch {
      /* ignore */
    }
    return `image-${Date.now()}.png`;
  }

  function collectGalleryUrls(app) {
    const urls = [];
    const seen = new Set();
    const add = (raw) => {
      const resolved = mediaFullUrl(raw);
      if (!resolved || seen.has(resolved)) return;
      seen.add(resolved);
      urls.push(resolved);
    };
    app.$.chatMessages.querySelectorAll('.message-images img').forEach((img) => {
      add(img.dataset.url || img.getAttribute('src'));
    });
    app.$.chatMessages.querySelectorAll('.message-bubble img, .md-inline-img').forEach((img) => {
      add(img.dataset.url || img.getAttribute('src'));
    });
    return urls;
  }

  function updateActions(app) {
    if (app.$.lightboxAttachCurrent) {
      app.$.lightboxAttachCurrent.disabled = !app.currentConvId;
    }
    if (app.$.lightboxFavorite) {
      const url = currentUrl(app);
      if (url) void app._syncFavoriteVisualByUrl(url, app.$.lightboxFavorite);
    }
  }

  function showAt(app, index) {
    if (!app._lightboxUrls.length) return;
    LightboxViewer.showAt(viewerCtx(app), index);
  }

  function step(app, delta) {
    LightboxViewer.step(viewerCtx(app), delta);
  }

  function open(app, url) {
    const resolved = mediaFullUrl(url);
    app._lightboxUrls = collectGalleryUrls(app);
    if (!app._lightboxUrls.length) {
      app._lightboxUrls = [resolved];
    }
    let index = app._lightboxUrls.indexOf(resolved);
    if (index < 0) {
      app._lightboxUrls.push(resolved);
      index = app._lightboxUrls.length - 1;
    }
    showAt(app, index);
    document.body.style.overflow = 'hidden';
  }

  function close(app) {
    LightboxViewer.close(viewerCtx(app));
    app.$.app?.removeAttribute('aria-hidden');
    app._lightboxUrls = [];
    app._lightboxIndex = 0;
    if (app.$.lightboxAttachCurrent) app.$.lightboxAttachCurrent.disabled = false;
    if (!app.$.convSidebar.classList.contains('open')) {
      document.body.style.overflow = '';
    }
    const restore = app._lightboxReturnFocus;
    app._lightboxReturnFocus = null;
    if (restore && typeof restore.focus === 'function') {
      try {
        restore.focus({ preventScroll: true });
      } catch {
        /* элемент мог быть удалён из DOM */
      }
    }
  }

  async function download(app) {
    const url = mediaFullUrl(currentUrl(app));
    if (!url) return;
    const btn = app.$.lightboxSave;
    if (btn) btn.disabled = true;
    try {
      await downloadMediaFile(url, filenameFromUrl(url));
    } catch (err) {
      app.showError(err.message || 'Не удалось скачать изображение');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function attachToComposer(app) {
    const url = currentUrl(app);
    if (!url) return;
    await app.attachImageUrlToComposer(url, app.$.lightboxAttachCurrent, { closeLightbox: true });
  }

  function bindEvents(app) {
    const ctx = viewerCtx(app);
    document.getElementById('lightbox-close')?.addEventListener('click', () => close(app));
    app.$.lightboxSave?.addEventListener('click', (e) => {
      e.stopPropagation();
      void download(app);
    });
    app.$.lightboxFavorite?.addEventListener('click', (e) => {
      e.stopPropagation();
      const url = currentUrl(app);
      if (url) void app._toggleFavoriteByUrl(url, app.$.lightboxFavorite);
    });
    app.$.lightboxPromote?.addEventListener('click', (e) => {
      e.stopPropagation();
      const url = currentUrl(app);
      if (url) void app._promoteToUploadsByUrl(url, app.$.lightboxPromote);
    });
    app.$.lightboxAttachCurrent?.addEventListener('click', (e) => {
      e.stopPropagation();
      void attachToComposer(app);
    });
    app.$.lightboxPrev?.addEventListener('click', (e) => {
      e.stopPropagation();
      step(app, -1);
    });
    app.$.lightboxNext?.addEventListener('click', (e) => {
      e.stopPropagation();
      step(app, 1);
    });
    app.$.lightbox?.addEventListener('click', (e) => {
      if (e.target === app.$.lightbox) close(app);
    });
    app.$.lightboxStage?.addEventListener('click', (e) => {
      if (e.target === app.$.lightboxStage) close(app);
    });
    LightboxViewer.bindTouch(ctx, app.$.lightbox);
  }

  function onDocumentKeydown(app, e) {
    if (LightboxViewer.onKeydown(viewerCtx(app), e)) return true;
    if (!isOpen(app)) return false;
    if (e.key === 'Escape') {
      e.preventDefault();
      close(app);
      return true;
    }
    return false;
  }

  window.WebChatLightbox = {
    isOpen,
    currentUrl,
    filenameFromUrl,
    open,
    close,
    step,
    download,
    attachToComposer,
    bindEvents,
    onDocumentKeydown,
  };
})();
