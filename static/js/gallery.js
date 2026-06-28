/**
 * Галерея генераций: сетка, lightbox, удаление, сохранение, автообновление.
 */
/* global escapeHtml, escapeAttr, promoteMediaToUploads, ICON_PROMOTE_TO_UPLOADS */

const POLL_MS = 5000;
const POLL_FALLBACK_MS = typeof SYSTEM_EVENTS_POLL_FALLBACK_MS === 'number'
  ? SYSTEM_EVENTS_POLL_FALLBACK_MS
  : 30000;
const GALLERY_LIMIT = 1000;
/** См. chat.js — восстановление вложений после перехода в чат */
const ICON_ATTACH =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
const ICON_SAVE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const ICON_DELETE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const ICON_STAR =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="12 2 15.1 8.5 22 9.3 17 14.1 18.3 21 12 17.5 5.7 21 7 14.1 2 9.3 8.9 8.5 12 2"/></svg>';
const ICON_PROMOTE = typeof ICON_PROMOTE_TO_UPLOADS === 'string' ? ICON_PROMOTE_TO_UPLOADS : '';

const $ = (sel, root = document) => root.querySelector(sel);

class GalleryApp {
  constructor() {
    this.items = [];
    this.itemById = new Map();
    this.lightboxIndex = -1;
    this.pollTimer = null;
    this._eventsSocket = null;
    this._eventsLive = false;
    this._pendingDeleteId = null;
    this._pendingDeleteBtn = null;

    this.els = {
      grid: $('#gallery-grid'),
      empty: $('#gallery-empty'),
      count: $('#gallery-count'),
      status: $('#gallery-status'),
      lightbox: $('#gallery-lightbox'),
      lightboxImg: $('#gallery-lightbox-img'),
      lightboxLoader: $('#gallery-lightbox-loader'),
      lightboxPrev: $('#gallery-lightbox-prev'),
      lightboxNext: $('#gallery-lightbox-next'),
      lightboxClose: $('#gallery-lightbox-close'),
      lightboxCounter: $('#gallery-lightbox-counter'),
      lightboxTitle: $('#gallery-lightbox-title'),
      lightboxSave: $('#gallery-lightbox-save'),
      lightboxFavorite: $('#gallery-lightbox-favorite'),
      lightboxAttach: $('#gallery-lightbox-attach'),
      lightboxPromote: $('#gallery-lightbox-promote'),
      lightboxDelete: $('#gallery-lightbox-delete'),
    };
  }

  init() {
    this.bindEvents();
    this._showGallerySkeleton();
    this.refresh(true);
    this._startPoll(POLL_MS);
    this._connectSystemEvents();
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) return;
      this.refresh(false);
    });
  }

  _startPoll(intervalMs) {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => this.refresh(false), intervalMs);
  }

  _connectSystemEvents() {
    if (typeof SystemEventsSocket !== 'function') return;
    this._eventsSocket = new SystemEventsSocket({
      onOpen: () => {
        this._eventsLive = true;
        this._startPoll(POLL_FALLBACK_MS);
      },
      onClose: () => {
        this._eventsLive = false;
        this._startPoll(POLL_MS);
      },
      onGalleryUpdate: (msg) => {
        if (msg?.kind === 'upload') return;
        this.refresh(false);
      },
    });
    this._eventsSocket.connect();
  }

  bindEvents() {
    const cleanupOrphansBtn = document.getElementById('gallery-cleanup-orphans');
    cleanupOrphansBtn?.addEventListener('click', () => void this.cleanupOrphans(cleanupOrphansBtn));

    const purgeAllBtn = document.getElementById('gallery-purge-all');
    purgeAllBtn?.addEventListener('click', () => void this.purgeAllGallery(purgeAllBtn));

    this.els.lightboxClose?.addEventListener('click', () => this.closeLightbox());
    this.els.lightboxPrev?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.stepLightbox(-1);
    });
    this.els.lightboxNext?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.stepLightbox(1);
    });
    this.els.lightbox?.addEventListener('click', (e) => {
      if (e.target === this.els.lightbox) this.closeLightbox();
    });
    this.els.lightboxSave?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.saveItem(this.currentItem());
    });
    this.els.lightboxFavorite?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) void this.toggleFavorite(item, this.els.lightboxFavorite);
    });
    this.els.lightboxAttach?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) void this.attachToNewChat(item, this.els.lightboxAttach);
    });
    this.els.lightboxPromote?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) void this.promoteToUploads(item, this.els.lightboxPromote);
    });
    this.els.lightboxDelete?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) this.onDeleteClick(item.id, this.els.lightboxDelete);
    });
    LightboxViewer.bindTouch(this._lightboxCtx(), this.els.lightbox);

    document.addEventListener('keydown', (e) => {
      if (LightboxViewer.onKeydown(this._lightboxCtx(), e)) return;
      if (!LightboxViewer.isOpen(this._lightboxCtx())) return;
      if (e.key === 'Escape') {
        this._cancelPendingDelete();
        this.closeLightbox();
      }
    });

    this.els.grid?.addEventListener('click', (e) => {
      const attachBtn = e.target.closest('.gallery-card-attach');
      if (attachBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = attachBtn.closest('.gallery-card');
        const item = card ? this.itemById.get(card.dataset.id) : null;
        if (item) void this.attachToNewChat(item, attachBtn);
        return;
      }

      const saveBtn = e.target.closest('.gallery-card-save');
      if (saveBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = saveBtn.closest('.gallery-card');
        const item = card ? this.itemById.get(card.dataset.id) : null;
        if (item) this.saveItem(item);
        return;
      }
      const promoteBtn = e.target.closest('.gallery-card-promote');
      if (promoteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = promoteBtn.closest('.gallery-card');
        const item = card ? this.itemById.get(card.dataset.id) : null;
        if (item) void this.promoteToUploads(item, promoteBtn);
        return;
      }

      const favoriteBtn = e.target.closest('.gallery-card-favorite');
      if (favoriteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = favoriteBtn.closest('.gallery-card');
        const item = card ? this.itemById.get(card.dataset.id) : null;
        if (item) void this.toggleFavorite(item, favoriteBtn);
        return;
      }

      const deleteBtn = e.target.closest('.gallery-card-delete');
      if (deleteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const card = deleteBtn.closest('.gallery-card');
        if (card) this.onDeleteClick(card.dataset.id, deleteBtn);
        return;
      }

      const card = e.target.closest('.gallery-card');
      if (!card || card.classList.contains('delete-pending')) return;
      const index = this.items.findIndex((it) => it.id === card.dataset.id);
      if (index >= 0) this.openLightbox(index);
    });

    document.addEventListener('click', (e) => {
      if (!this._pendingDeleteId) return;
      if (e.target.closest(`.gallery-card-delete[data-id="${this._pendingDeleteId}"]`)) return;
      if (e.target.closest('#gallery-lightbox-delete')) return;
      this._cancelPendingDelete();
    });
  }

  onDeleteClick(id, btn) {
    if (this._pendingDeleteId === id) {
      void this.executeDeleteItem(this.itemById.get(id));
      return;
    }
    this._cancelPendingDelete();
    this._pendingDeleteId = id;
    this._pendingDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    this._findCard(id)?.classList.add('delete-pending');
    this._syncLightboxDeleteArmed(id);
  }

  _cancelPendingDelete() {
    if (!this._pendingDeleteId) return;
    this._pendingDeleteBtn?.classList.remove('delete-armed');
    this._findCard(this._pendingDeleteId)?.classList.remove('delete-pending');
    if (this._pendingDeleteBtn) {
      const isCard = this._pendingDeleteBtn.classList.contains('gallery-card-delete');
      this._pendingDeleteBtn.title = isCard ? 'Удалить' : 'Удалить';
    }
    if (this.els.lightboxDelete) {
      this.els.lightboxDelete.classList.remove('delete-armed');
      this.els.lightboxDelete.title = 'Удалить';
    }
    this._pendingDeleteId = null;
    this._pendingDeleteBtn = null;
  }

  _findCard(id) {
    return this.els.grid?.querySelector(`.gallery-card[data-id="${CSS.escape(id)}"]`);
  }

  _syncLightboxDeleteArmed(id) {
    if (!this.els.lightboxDelete || this.els.lightbox?.classList.contains('hidden')) return;
    const current = this.currentItem();
    if (current?.id === id) {
      this.els.lightboxDelete.classList.add('delete-armed');
      this.els.lightboxDelete.title = 'Нажмите ещё раз для удаления';
    }
  }

  _showGallerySkeleton(count = 8) {
    if (!this.els.grid) return;
    this.els.empty?.classList.add('hidden');
    this.els.grid.classList.remove('hidden');
    this.els.grid.innerHTML = '';
    for (let i = 0; i < count; i += 1) {
      const card = document.createElement('div');
      card.className = 'gallery-skeleton-card';
      card.setAttribute('aria-hidden', 'true');
      this.els.grid.appendChild(card);
    }
    this.els.grid.setAttribute('aria-busy', 'true');
  }

  async refresh(initial) {
    try {
      const res = await fetch(`/api/gallery?limit=${GALLERY_LIMIT}`, {
        credentials: 'same-origin',
      });
      if (res.status === 401) {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.replace(`/login?next=${next}`);
        return;
      }
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      const incoming = data.images || [];
      const prevIds = new Set(this.items.map((i) => i.id));
      const newIds = incoming.filter((i) => !prevIds.has(i.id));
      const removed = this.items.length !== incoming.length
        || this.items.some((i, idx) => incoming[idx]?.id !== i.id);

      this.items = incoming;
      this.itemById = new Map(incoming.map((i) => [i.id, i]));

      if (removed || initial) {
        this.renderGrid();
      } else if (newIds.length) {
        this.prependCards(newIds);
      }

      this.updateCount(incoming.length);
      if (!initial && newIds.length) {
        this.flashStatus(`+${newIds.length} новых`);
      }
      this.syncLightboxAfterRefresh();
    } catch (err) {
      this.els.grid?.removeAttribute('aria-busy');
      this.updateCount(this.items.length);
      this.flashStatus(`Ошибка: ${err.message}`, true);
    }
  }

  updateCount(n) {
    if (this.els.count) this.els.count.textContent = String(n);
    if (this.els.empty) this.els.empty.classList.toggle('hidden', n > 0);
    if (this.els.grid) this.els.grid.classList.toggle('hidden', n === 0);
  }

  flashStatus(text, isError = false) {
    if (!this.els.status) return;
    this.els.status.textContent = text;
    this.els.status.classList.toggle('is-error', isError);
    this.els.status.classList.add('is-visible');
    clearTimeout(this._statusTimer);
    this._statusTimer = setTimeout(() => {
      this.els.status.classList.remove('is-visible');
    }, 2800);
  }

  renderGrid() {
    if (!this.els.grid) return;
    this._cancelPendingDelete();
    this.els.grid.removeAttribute('aria-busy');
    this.els.grid.innerHTML = '';
    const frag = document.createDocumentFragment();
    for (const item of this.items) {
      frag.appendChild(this.createCard(item));
    }
    this.els.grid.appendChild(frag);
  }

  prependCards(newItems) {
    if (!this.els.grid) return;
    const frag = document.createDocumentFragment();
    for (let i = newItems.length - 1; i >= 0; i--) {
      frag.appendChild(this.createCard(newItems[i]));
    }
    this.els.grid.prepend(frag);
  }

  createCard(item) {
    const card = document.createElement('div');
    card.className = 'gallery-card';
    card.dataset.id = item.id;
    card.innerHTML = `
      <div class="gallery-card-media">
        <img src="${escapeAttr(mediaPreviewUrl(item.thumb_url))}" alt="${escapeAttr(item.filename)}" loading="lazy" decoding="async">
        <button type="button" class="gallery-card-action gallery-card-attach gallery-card-attach-tl" data-id="${escapeAttr(item.id)}" title="Новый чат с этим изображением" aria-label="Прикрепить в новый чат">${ICON_ATTACH}</button>
        <div class="gallery-card-actions">
          <button type="button" class="gallery-card-action gallery-card-favorite${item.is_favorite ? ' is-favorite' : ''}" data-id="${escapeAttr(item.id)}" title="${item.is_favorite ? 'Убрать из избранного' : 'В избранное'}" aria-label="Избранное">${ICON_STAR}</button>
          <button type="button" class="gallery-card-action gallery-card-promote" data-id="${escapeAttr(item.id)}" title="В галерею загрузок" aria-label="В галерею загрузок">${ICON_PROMOTE}</button>
          <button type="button" class="gallery-card-action gallery-card-save" data-id="${escapeAttr(item.id)}" title="Сохранить" aria-label="Сохранить">${ICON_SAVE}</button>
          <button type="button" class="gallery-card-action gallery-card-delete danger" data-id="${escapeAttr(item.id)}" title="Удалить" aria-label="Удалить">${ICON_DELETE}</button>
        </div>
      </div>
      <span class="gallery-card-meta">${escapeHtml(item.filename)} · ${item.size_kb} KB</span>
    `;
    return card;
  }

  _lightboxCtx() {
    const g = this;
    return {
      root: g.els.lightbox,
      img: g.els.lightboxImg,
      loader: g.els.lightboxLoader,
      prev: g.els.lightboxPrev,
      next: g.els.lightboxNext,
      counter: g.els.lightboxCounter,
      get index() {
        return g.lightboxIndex;
      },
      set index(v) {
        g.lightboxIndex = v;
      },
      getCount: () => g.items.length,
      getUrl: (i) => g.items[i]?.url || null,
      onShow: (i) => {
        const item = g.items[i];
        if (!item) return;
        if (g.els.lightboxImg) g.els.lightboxImg.alt = item.filename;
        if (g.els.lightboxTitle) g.els.lightboxTitle.textContent = item.filename;
        g._syncFavoriteButton(item);
      },
    };
  }

  openLightbox(index) {
    if (!this.items.length) return;
    this._cancelPendingDelete();
    const i = Math.max(0, Math.min(index, this.items.length - 1));
    LightboxViewer.showAt(this._lightboxCtx(), i);
    document.body.classList.add('gallery-lightbox-open');
  }

  showLightboxAt(index) {
    if (!this.items.length || !this.els.lightbox) return;
    LightboxViewer.showAt(this._lightboxCtx(), index);
  }

  _syncFavoriteButton(item) {
    const btn = this.els.lightboxFavorite;
    if (!btn || !item) return;
    btn.classList.toggle('is-favorite', Boolean(item.is_favorite));
    btn.title = item.is_favorite ? 'Убрать из избранного' : 'В избранное';
    btn.setAttribute('aria-label', item.is_favorite ? 'Убрать из избранного' : 'В избранное');
  }

  updateLightboxNav() {
    LightboxViewer.updateNav(this._lightboxCtx());
  }

  stepLightbox(delta) {
    if (!LightboxViewer.isOpen(this._lightboxCtx())) return;
    this._cancelPendingDelete();
    LightboxViewer.step(this._lightboxCtx(), delta);
  }

  closeLightbox() {
    this._cancelPendingDelete();
    LightboxViewer.close(this._lightboxCtx());
    this.lightboxIndex = -1;
    document.body.classList.remove('gallery-lightbox-open');
  }

  syncLightboxAfterRefresh() {
    if (this.els.lightbox?.classList.contains('hidden')) return;
    if (this.lightboxIndex < 0 || this.lightboxIndex >= this.items.length) {
      if (this.items.length) this.showLightboxAt(Math.min(this.lightboxIndex, this.items.length - 1));
      else this.closeLightbox();
      return;
    }
    this.showLightboxAt(this.lightboxIndex);
  }

  currentItem() {
    return this.items[this.lightboxIndex] || null;
  }

  async toggleFavorite(item, btn) {
    if (!item) return;
    const next = !item.is_favorite;
    const prev = item.is_favorite;
    item.is_favorite = next;
    item.favorite_at = next ? Date.now() / 1000 : null;
    if (btn) this._applyFavoriteVisual(btn, next);
    this._syncFavoriteButton(item);
    const cardBtn = document.querySelector(`.gallery-card-favorite[data-id="${CSS.escape(item.id)}"]`);
    if (cardBtn && cardBtn !== btn) this._applyFavoriteVisual(cardBtn, next);
    try {
      const res = await fetch('/api/gallery/favorite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: item.source,
          id: item.id,
          favorite: next,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      await this.refresh(false);
      this.flashStatus(next ? 'Добавлено в избранное' : 'Удалено из избранного');
    } catch (err) {
      item.is_favorite = prev;
      item.favorite_at = prev ? Date.now() / 1000 : null;
      if (btn) this._applyFavoriteVisual(btn, prev);
      this._syncFavoriteButton(item);
      if (cardBtn && cardBtn !== btn) this._applyFavoriteVisual(cardBtn, prev);
      this.flashStatus(err.message || 'Не удалось обновить избранное', true);
    }
  }

  _applyFavoriteVisual(btn, isFav) {
    btn.classList.toggle('is-favorite', Boolean(isFav));
    btn.title = isFav ? 'Убрать из избранного' : 'В избранное';
    btn.setAttribute('aria-label', isFav ? 'Убрать из избранного' : 'В избранное');
  }

  async attachToNewChat(item, btn) {
    if (!window.GalleryCommon?.attachImageToNewChat) {
      this.flashStatus('Не загружен gallery-common.js', true);
      return;
    }
    const commentEl = document.getElementById('gallery-lightbox-comment');
    await window.GalleryCommon.attachImageToNewChat(item, {
      btn,
      userComment: commentEl?.value?.trim() || '',
      onStatus: (text, isError) => this.flashStatus(text, Boolean(isError)),
    });
  }

  async promoteToUploads(item, btn) {
    if (!item?.url) {
      this.flashStatus('Нет адреса изображения', true);
      return;
    }
    const prevDisabled = btn?.disabled;
    if (btn) btn.disabled = true;
    try {
      await promoteMediaToUploads(item.url);
      this.flashStatus('Добавлено в галерею загрузок');
    } catch (err) {
      this.flashStatus(err.message || 'Ошибка', true);
    } finally {
      if (btn) btn.disabled = prevDisabled ?? false;
    }
  }

  async saveItem(item) {
    if (!item) return;
    try {
      await downloadMediaFile(item.url, item.filename);
      this.flashStatus('Сохранено');
    } catch (err) {
      this.flashStatus(err.message, true);
    }
  }

  async cleanupOrphans(btn) {
    const prevText = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Проверка…';
    }
    try {
      const previewRes = await fetch('/api/gallery/cleanup-orphans?dry_run=true');
      if (!previewRes.ok) {
        const body = await previewRes.json().catch(() => ({}));
        throw new Error(body.detail || previewRes.statusText);
      }
      const preview = await previewRes.json();
      const diskN = preview.disk?.would_delete ?? preview.disk?.candidates?.length ?? 0;
      const dbN = preview.db?.would_delete ?? preview.db?.candidates?.length ?? 0;
      const total = diskN + dbN;
      if (total === 0) {
        this.flashStatus('Сирот не найдено');
        return;
      }
      const ok = confirm(
        `Удалить сироты?\n\n`
        + `Файлы на диске: ${diskN}\n`
        + `Записи в БД (без ссылок в чате): ${dbN}\n\n`
        + 'Изображения, используемые в сообщениях, не затрагиваются.',
      );
      if (!ok) return;

      if (btn) btn.textContent = 'Очистка…';
      const res = await fetch('/api/gallery/cleanup-orphans?dedup_db=true', { method: 'POST' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      const data = await res.json();
      const removed = (data.disk?.deleted ?? 0) + (data.db?.deleted ?? 0);
      await this.refresh(true);
      this.flashStatus(`Удалено сирот: ${removed}`);
    } catch (err) {
      this.flashStatus(err.message || 'Ошибка', true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prevText || 'Очистить сироты';
      }
    }
  }

  async purgeAllGallery(btn) {
    const n = this.items.length;
    if (n === 0) {
      this.flashStatus('Галерея уже пуста');
      return;
    }
    const ok = confirm(
      `Удалить все ${n} изображений из галереи?\n\n`
      + 'Ссылки на них в сообщениях чата также будут убраны. Это действие нельзя отменить.',
    );
    if (!ok) return;

    const prevText = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Удаление…';
    }
    try {
      const res = await fetch('/api/gallery/all?purge_messages=true', { method: 'DELETE' });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      const data = await res.json();
      this.closeLightbox();
      this.items = [];
      this.itemById.clear();
      this.renderGrid();
      this.updateCount(0);
      this.flashStatus(`Удалено: ${data.total ?? n}`);
    } catch (err) {
      this.flashStatus(err.message, true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prevText || 'Очистить галерею';
      }
    }
  }

  async executeDeleteItem(item) {
    if (!item) return;
    this._cancelPendingDelete();

    const path = item.source === 'db'
      ? `/api/gallery/db/${item.id}`
      : `/api/gallery/disk/${encodeURIComponent(item.filename)}`;

    const snapshot = {
      items: [...this.items],
      lightboxIndex: this.lightboxIndex,
    };
    this.items = this.items.filter((i) => i.id !== item.id);
    this.itemById.delete(item.id);
    this.renderGrid();
    this.updateCount(this.items.length);
    if (this.els.lightbox && !this.els.lightbox.classList.contains('hidden')) {
      if (!this.items.length) this.closeLightbox();
      else if (this.lightboxIndex >= this.items.length) {
        this.showLightboxAt(this.items.length - 1);
      } else {
        this.showLightboxAt(this.lightboxIndex);
      }
    }

    try {
      const res = await fetch(path, { method: 'DELETE' });
      if (res.status === 404) throw new Error('Уже удалено');
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      this.flashStatus('Удалено');
    } catch (err) {
      this.items = snapshot.items;
      this.itemById.clear();
      for (const i of this.items) this.itemById.set(i.id, i);
      this.renderGrid();
      this.updateCount(this.items.length);
      if (this.els.lightbox && !this.els.lightbox.classList.contains('hidden')) {
        const idx = this.items.findIndex((i) => i.id === item.id);
        if (idx >= 0) this.showLightboxAt(idx);
        else this.closeLightbox();
      }
      this.flashStatus(err.message, true);
    }
  }

}

document.addEventListener('DOMContentLoaded', () => {
  const app = new GalleryApp();
  app.init();
});
