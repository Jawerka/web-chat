/**
 * Галерея генераций: сетка, lightbox, удаление, сохранение, автообновление.
 */

const POLL_MS = 5000;
const POLL_FALLBACK_MS = typeof SYSTEM_EVENTS_POLL_FALLBACK_MS === 'number'
  ? SYSTEM_EVENTS_POLL_FALLBACK_MS
  : 30000;
const GALLERY_LIMIT = 1000;
/** См. chat.js — восстановление вложений после перехода в чат */
const PENDING_ATTACHMENTS_KEY = 'webchat_pending_attachments';
const DEFAULT_CONV_TITLE = 'Новая беседа';
const IMG2IMG_PRESET_SLUG = 'img2img';

const ICON_ATTACH =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
const ICON_SAVE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const ICON_DELETE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';

const $ = (sel, root = document) => root.querySelector(sel);

class GalleryApp {
  constructor() {
    this.items = [];
    this.itemById = new Map();
    this.lightboxIndex = -1;
    this.pollTimer = null;
    this._eventsSocket = null;
    this._eventsLive = false;
    this.touchStart = null;
    this._pendingDeleteId = null;
    this._pendingDeleteBtn = null;

    this.els = {
      grid: $('#gallery-grid'),
      empty: $('#gallery-empty'),
      count: $('#gallery-count'),
      status: $('#gallery-status'),
      lightbox: $('#gallery-lightbox'),
      lightboxImg: $('#gallery-lightbox-img'),
      lightboxPrev: $('#gallery-lightbox-prev'),
      lightboxNext: $('#gallery-lightbox-next'),
      lightboxClose: $('#gallery-lightbox-close'),
      lightboxCounter: $('#gallery-lightbox-counter'),
      lightboxTitle: $('#gallery-lightbox-title'),
      lightboxSave: $('#gallery-lightbox-save'),
      lightboxAttach: $('#gallery-lightbox-attach'),
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
      onGalleryUpdate: () => this.refresh(false),
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
    this.els.lightboxAttach?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) void this.attachToNewChat(item, this.els.lightboxAttach);
    });
    this.els.lightboxDelete?.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = this.currentItem();
      if (item) this.onDeleteClick(item.id, this.els.lightboxDelete);
    });
    this.els.lightbox?.addEventListener('touchstart', (e) => this.onTouchStart(e), { passive: true });
    this.els.lightbox?.addEventListener('touchend', (e) => this.onTouchEnd(e), { passive: true });

    document.addEventListener('keydown', (e) => {
      if (this.els.lightbox?.classList.contains('hidden')) return;
      if (e.key === 'Escape') {
        this._cancelPendingDelete();
        this.closeLightbox();
      } else if (e.key === 'ArrowLeft') this.stepLightbox(-1);
      else if (e.key === 'ArrowRight') this.stepLightbox(1);
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
      const res = await fetch(`/api/gallery?limit=${GALLERY_LIMIT}`);
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
        <img src="${this.escapeAttr(mediaPreviewUrl(item.thumb_url))}" alt="${this.escapeAttr(item.filename)}" loading="lazy" decoding="async">
        <button type="button" class="gallery-card-action gallery-card-attach gallery-card-attach-tl" data-id="${this.escapeAttr(item.id)}" title="Новый чат с этим изображением" aria-label="Прикрепить в новый чат">${ICON_ATTACH}</button>
        <div class="gallery-card-actions">
          <button type="button" class="gallery-card-action gallery-card-save" data-id="${this.escapeAttr(item.id)}" title="Сохранить" aria-label="Сохранить">${ICON_SAVE}</button>
          <button type="button" class="gallery-card-action gallery-card-delete danger" data-id="${this.escapeAttr(item.id)}" title="Удалить" aria-label="Удалить">${ICON_DELETE}</button>
        </div>
      </div>
      <span class="gallery-card-meta">${this.escapeHtml(item.filename)} · ${item.size_kb} KB</span>
    `;
    return card;
  }

  openLightbox(index) {
    if (!this.items.length) return;
    this._cancelPendingDelete();
    this.lightboxIndex = Math.max(0, Math.min(index, this.items.length - 1));
    this.showLightboxAt(this.lightboxIndex);
    document.body.classList.add('gallery-lightbox-open');
  }

  showLightboxAt(index) {
    const item = this.items[index];
    if (!item || !this.els.lightbox) return;
    this.lightboxIndex = index;
    this.els.lightboxImg.src = item.url;
    this.els.lightboxImg.alt = item.filename;
    if (this.els.lightboxTitle) {
      this.els.lightboxTitle.textContent = item.filename;
    }
    this.els.lightbox.classList.remove('hidden');
    this.updateLightboxNav();
  }

  updateLightboxNav() {
    const n = this.items.length;
    const i = this.lightboxIndex;
    if (this.els.lightboxPrev) this.els.lightboxPrev.disabled = i <= 0;
    if (this.els.lightboxNext) this.els.lightboxNext.disabled = i >= n - 1;
    if (this.els.lightboxCounter) {
      if (n > 1) {
        this.els.lightboxCounter.textContent = `${i + 1} / ${n}`;
        this.els.lightboxCounter.classList.remove('hidden');
      } else {
        this.els.lightboxCounter.classList.add('hidden');
      }
    }
  }

  stepLightbox(delta) {
    if (this.els.lightbox?.classList.contains('hidden')) return;
    this._cancelPendingDelete();
    const next = this.lightboxIndex + delta;
    if (next < 0 || next >= this.items.length) return;
    this.showLightboxAt(next);
  }

  onTouchStart(e) {
    if (e.touches.length !== 1) return;
    this.touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
  }

  onTouchEnd(e) {
    if (!this.touchStart || e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - this.touchStart.x;
    const dy = e.changedTouches[0].clientY - this.touchStart.y;
    this.touchStart = null;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy)) return;
    this.stepLightbox(dx < 0 ? 1 : -1);
  }

  closeLightbox() {
    this._cancelPendingDelete();
    this.els.lightbox?.classList.add('hidden');
    if (this.els.lightboxImg) this.els.lightboxImg.src = '';
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

  async attachToNewChat(item, btn) {
    if (!item?.url) return;
    const prevDisabled = btn?.disabled;
    if (btn) btn.disabled = true;
    this.flashStatus('Создаём чат…');
    try {
      const presetsRes = await fetch('/api/presets');
      if (!presetsRes.ok) throw new Error('Не удалось загрузить пресеты');
      const presets = await presetsRes.json();
      const img2imgPreset = presets.find((p) => p.slug === IMG2IMG_PRESET_SLUG);

      const convBody = { title: DEFAULT_CONV_TITLE };
      if (img2imgPreset?.id) convBody.preset_id = img2imgPreset.id;

      const convRes = await fetch('/api/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(convBody),
      });
      if (!convRes.ok) {
        const errBody = await convRes.json().catch(() => ({}));
        throw new Error(errBody.detail || convRes.statusText);
      }
      const conv = await convRes.json();

      const imgRes = await fetch(item.url);
      if (!imgRes.ok) throw new Error('Не удалось загрузить изображение');
      const blob = await imgRes.blob();
      const mime = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
      const file = new File([blob], item.filename || 'image.png', { type: mime });

      const fd = new FormData();
      fd.append('files', file);
      fd.append('conversation_id', conv.id);

      const upRes = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!upRes.ok) {
        const errBody = await upRes.json().catch(() => ({}));
        throw new Error(errBody.detail || 'Ошибка загрузки вложения');
      }
      const uploadData = await upRes.json();

      sessionStorage.setItem(
        PENDING_ATTACHMENTS_KEY,
        JSON.stringify({
          conversation_id: conv.id,
          attachments: uploadData.attachments || [],
        }),
      );
      localStorage.setItem('webchat_conv_id', conv.id);
      window.location.href = '/';
    } catch (err) {
      this.flashStatus(err.message || 'Ошибка', true);
      if (btn) btn.disabled = prevDisabled ?? false;
    }
  }

  async saveItem(item) {
    if (!item) return;
    try {
      const res = await fetch(item.url);
      if (!res.ok) throw new Error('Не удалось загрузить файл');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = item.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
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

  escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  escapeAttr(s) {
    return String(s).replace(/"/g, '&quot;');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new GalleryApp();
  app.init();
});
