/**
 * Галерея загрузок: сетка как /gallery; лайтбокс ref-layout по клику на карточку.
 */
/* global escapeHtml, escapeAttr, mediaPreviewUrl, downloadMediaFile */

const UPLOADS_LIMIT = 5000;
const CARD_DRAG_MIME = 'application/x-upload-card-id';
const ICON_GRIP =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/></svg>';

const ICON_SAVE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const ICON_DELETE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const ICON_STAR =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="12 2 15.1 8.5 22 9.3 17 14.1 18.3 21 12 17.5 5.7 21 7 14.1 2 9.3 8.9 8.5 12 2"/></svg>';
const ICON_ATTACH =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';

const $ = (sel, root = document) => root.querySelector(sel);

class GalleryUploadsApp {
  constructor() {
    this.items = [];
    this.itemById = new Map();
    this._pendingDeleteId = null;
    this._pendingDeleteBtn = null;
    this._dragCardId = null;
    this._reorderSaving = false;
    this.lightbox = new UploadsRefLightbox();
    this.lightbox.onExtract = (item) => void this.extractMetadata(item);
    this.lightbox.onAttach = (item, btn) => void this.attachToNewChat(item, btn);
    this.els = {
      grid: $('#uploads-grid'),
      empty: $('#uploads-empty'),
      count: $('#uploads-count'),
      status: $('#uploads-status'),
      fileInput: $('#uploads-file-input'),
      uploadBtn: $('#uploads-upload-btn'),
      emptyUploadBtn: $('#uploads-empty-upload'),
      page: $('.gallery-page'),
    };
  }

  init() {
    const openPicker = () => this.els.fileInput?.click();
    this.els.uploadBtn?.addEventListener('click', openPicker);
    this.els.emptyUploadBtn?.addEventListener('click', openPicker);
    this.els.fileInput?.addEventListener('change', () => {
      if (this.els.fileInput?.files?.length) void this.uploadFiles(this.els.fileInput.files);
      this.els.fileInput.value = '';
    });
    this.bindDropzone();
    this.bindGrid();
    this.bindReorder();
    this.bindSystemEvents();
    void this.refresh(true);
    this.openHashIfAny();
    window.addEventListener('hashchange', () => this.openHashIfAny());
  }

  bindSystemEvents() {
    if (typeof SystemEventsSocket !== 'function') return;
    this._eventsSocket = new SystemEventsSocket({
      onGalleryUpdate: (msg) => {
        if (msg?.kind && msg.kind !== 'upload') return;
        if (this._reorderSaving || this._dragCardId) return;
        void this.refresh(false);
      },
    });
    this._eventsSocket.connect();
  }

  _isCardReorderDrag(e) {
    const types = e.dataTransfer?.types;
    if (!types) return false;
    return Array.from(types).includes(CARD_DRAG_MIME);
  }

  bindDropzone() {
    const root = this.els.page;
    if (!root) return;
    ['dragenter', 'dragover'].forEach((ev) => {
      root.addEventListener(ev, (e) => {
        if (this._isCardReorderDrag(e)) return;
        e.preventDefault();
        root.classList.add('uploads-dragover');
      });
    });
    ['dragleave', 'drop'].forEach((ev) => {
      root.addEventListener(ev, (e) => {
        if (this._isCardReorderDrag(e)) return;
        e.preventDefault();
        if (ev === 'dragleave' && root.contains(e.relatedTarget)) return;
        root.classList.remove('uploads-dragover');
      });
    });
    root.addEventListener('drop', (e) => {
      if (this._isCardReorderDrag(e)) return;
      const files = e.dataTransfer?.files;
      if (files?.length) void this.uploadFiles(files);
    });
  }

  bindReorder() {
    const grid = this.els.grid;
    if (!grid || grid.dataset.reorderBound) return;
    grid.dataset.reorderBound = '1';

    grid.addEventListener('dragstart', (e) => {
      const handle = e.target.closest('.gallery-card-drag');
      const card = handle?.closest('.gallery-card');
      if (!handle || !card) return;
      this._dragCardId = card.dataset.id;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData(CARD_DRAG_MIME, this._dragCardId);
      card.classList.add('is-dragging');
      grid.classList.add('is-reordering');
    });

    grid.addEventListener('dragend', (e) => {
      const card = e.target.closest('.gallery-card');
      card?.classList.remove('is-dragging');
      grid.classList.remove('is-reordering');
      grid.querySelectorAll('.gallery-card.drop-before').forEach((el) => {
        el.classList.remove('drop-before');
      });
      this._dragCardId = null;
    });

    grid.addEventListener('dragover', (e) => {
      if (!this._isCardReorderDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      const card = e.target.closest('.gallery-card');
      grid.querySelectorAll('.gallery-card.drop-before').forEach((el) => {
        el.classList.remove('drop-before');
      });
      if (card && card.dataset.id !== this._dragCardId) {
        card.classList.add('drop-before');
      }
    });

    grid.addEventListener('dragleave', (e) => {
      const card = e.target.closest('.gallery-card');
      if (card && !card.contains(e.relatedTarget)) {
        card.classList.remove('drop-before');
      }
    });

    grid.addEventListener('drop', (e) => {
      if (!this._isCardReorderDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      const dragId = e.dataTransfer.getData(CARD_DRAG_MIME) || this._dragCardId;
      const targetCard = e.target.closest('.gallery-card');
      grid.querySelectorAll('.gallery-card.drop-before').forEach((el) => {
        el.classList.remove('drop-before');
      });
      if (!dragId || !targetCard || targetCard.dataset.id === dragId) return;
      void this.reorderItems(dragId, targetCard.dataset.id);
    });
  }

  reorderItems(dragId, targetId) {
    const fromIdx = this.items.findIndex((i) => i.id === dragId);
    const toIdx = this.items.findIndex((i) => i.id === targetId);
    if (fromIdx < 0 || toIdx < 0 || fromIdx === toIdx) return;
    const snapshot = this.items.map((i) => ({ ...i }));
    const [moved] = this.items.splice(fromIdx, 1);
    const insertIdx = this.items.findIndex((i) => i.id === targetId);
    this.items.splice(insertIdx, 0, moved);
    this.items.forEach((item, index) => {
      item.gallery_sort_order = index;
    });
    this.itemById = new Map(this.items.map((i) => [i.id, i]));
    this.lightbox.setItems(this.items);
    this.renderGrid();
    void this.persistOrder(snapshot);
  }

  async persistOrder(snapshot) {
    if (this._reorderSaving) return;
    this._reorderSaving = true;
    const ids = this.items.map((i) => i.id);
    try {
      const res = await fetch('/api/gallery/uploads/reorder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
    } catch (err) {
      this.items = snapshot;
      this.itemById = new Map(this.items.map((i) => [i.id, i]));
      this.lightbox.setItems(this.items);
      this.renderGrid();
      this.flashStatus(err.message || 'Не удалось сохранить порядок', true);
    } finally {
      this._reorderSaving = false;
    }
  }

  bindGrid() {
    this.els.grid?.addEventListener('click', (e) => {
      const attachBtn = e.target.closest('.gallery-card-attach');
      if (attachBtn) {
        e.preventDefault();
        e.stopPropagation();
        const item = this._itemFromCard(attachBtn);
        if (item) void this.attachToNewChat(item, attachBtn);
        return;
      }
      const saveBtn = e.target.closest('.gallery-card-save');
      if (saveBtn) {
        e.preventDefault();
        e.stopPropagation();
        const item = this._itemFromCard(saveBtn);
        if (item) void this.saveItem(item);
        return;
      }
      const favoriteBtn = e.target.closest('.gallery-card-favorite');
      if (favoriteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const item = this._itemFromCard(favoriteBtn);
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
      if (index >= 0) this.lightbox.open(index);
    });

    document.addEventListener('click', (e) => {
      if (!this._pendingDeleteId) return;
      if (e.target.closest(`.gallery-card-delete[data-id="${this._pendingDeleteId}"]`)) return;
      this._cancelPendingDelete();
    });
  }

  _itemFromCard(el) {
    const card = el.closest('.gallery-card');
    return card ? this.itemById.get(card.dataset.id) : null;
  }

  flashStatus(text, isError = false) {
    if (!this.els.status) return;
    this.els.status.textContent = text;
    this.els.status.classList.toggle('is-error', isError);
    this.els.status.classList.add('is-visible');
    clearTimeout(this._statusTimer);
    this._statusTimer = setTimeout(() => {
      this.els.status?.classList.remove('is-visible');
    }, 2800);
  }

  _showSkeleton(count = 8) {
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
    if (initial) this._showSkeleton();
    try {
      const res = await fetch(`/api/gallery/uploads?limit=${UPLOADS_LIMIT}`);
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      this.items = data.images || [];
      this.itemById = new Map(this.items.map((i) => [i.id, i]));
      this.lightbox.setItems(this.items);
      this.renderGrid();
      if (this.els.count) this.els.count.textContent = String(this.items.length);
      if (!initial) this.flashStatus('Обновлено');
    } catch (err) {
      this.flashStatus(err.message || 'Ошибка', true);
    }
  }

  updateLayout() {
    const n = this.items.length;
    this.els.empty?.classList.toggle('hidden', n > 0);
    this.els.grid?.classList.toggle('hidden', n === 0);
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
    this.updateLayout();
  }

  createCard(item) {
    const card = document.createElement('div');
    card.className = 'gallery-card';
    card.dataset.id = item.id;
    const thumb = typeof mediaPreviewUrl === 'function'
      ? mediaPreviewUrl(item.thumb_url || item.url)
      : (item.thumb_url || item.url);
    card.innerHTML = `
      <div class="gallery-card-media">
        <img src="${escapeAttr(thumb)}" alt="${escapeAttr(item.filename || '')}" loading="lazy" decoding="async">
        <button type="button" class="gallery-card-drag" draggable="true" title="Перетащите для изменения порядка" aria-label="Изменить порядок">${ICON_GRIP}</button>
        <button type="button" class="gallery-card-action gallery-card-attach gallery-card-attach-tl" data-id="${escapeAttr(item.id)}" title="Новый чат с этим изображением" aria-label="Прикрепить в новый чат">${ICON_ATTACH}</button>
        <div class="gallery-card-actions">
          <button type="button" class="gallery-card-action gallery-card-favorite${item.is_favorite ? ' is-favorite' : ''}" data-id="${escapeAttr(item.id)}" title="${item.is_favorite ? 'Убрать из избранного' : 'В избранное'}" aria-label="Избранное">${ICON_STAR}</button>
          <button type="button" class="gallery-card-action gallery-card-save" data-id="${escapeAttr(item.id)}" title="Сохранить" aria-label="Сохранить">${ICON_SAVE}</button>
          <button type="button" class="gallery-card-action gallery-card-delete danger" data-id="${escapeAttr(item.id)}" title="Удалить" aria-label="Удалить">${ICON_DELETE}</button>
        </div>
      </div>
      <span class="gallery-card-meta">${escapeHtml(item.filename || item.id)} · ${item.size_kb} KB</span>
    `;
    return card;
  }

  _applyFavoriteVisual(btn, on) {
    btn.classList.toggle('is-favorite', on);
    btn.title = on ? 'Убрать из избранного' : 'В избранное';
    btn.setAttribute('aria-label', on ? 'Убрать из избранного' : 'В избранное');
  }

  async toggleFavorite(item, btn) {
    if (!item) return;
    const next = !item.is_favorite;
    const prev = item.is_favorite;
    item.is_favorite = next;
    if (btn) this._applyFavoriteVisual(btn, next);
    const cardBtn = document.querySelector(`.gallery-card-favorite[data-id="${CSS.escape(item.id)}"]`);
    if (cardBtn && cardBtn !== btn) this._applyFavoriteVisual(cardBtn, next);
    try {
      const res = await fetch('/api/gallery/uploads/favorite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: item.id, favorite: next }),
      });
      if (!res.ok) throw new Error(res.statusText);
    } catch (err) {
      item.is_favorite = prev;
      if (btn) this._applyFavoriteVisual(btn, prev);
      if (cardBtn && cardBtn !== btn) this._applyFavoriteVisual(cardBtn, prev);
      this.flashStatus(err.message, true);
    }
  }

  onDeleteClick(id, btn) {
    if (this._pendingDeleteId === id) {
      void this.executeDelete(id);
      return;
    }
    this._cancelPendingDelete();
    this._pendingDeleteId = id;
    this._pendingDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    this._findCard(id)?.classList.add('delete-pending');
  }

  _cancelPendingDelete() {
    if (!this._pendingDeleteId) return;
    this._pendingDeleteBtn?.classList.remove('delete-armed');
    this._findCard(this._pendingDeleteId)?.classList.remove('delete-pending');
    this._pendingDeleteId = null;
    this._pendingDeleteBtn = null;
  }

  _findCard(id) {
    return this.els.grid?.querySelector(`.gallery-card[data-id="${CSS.escape(id)}"]`);
  }

  async executeDelete(id) {
    if (!id) return;
    this._cancelPendingDelete();
    try {
      const res = await fetch(`/api/gallery/uploads/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(res.statusText);
      this.lightbox.close();
      await this.refresh(false);
      this.flashStatus('Удалено');
    } catch (err) {
      this.flashStatus(err.message, true);
    }
  }

  async attachToNewChat(item, btn) {
    if (!window.GalleryCommon?.attachImageToNewChat) {
      this.flashStatus('Не загружен gallery-common.js', true);
      return;
    }
    await window.GalleryCommon.attachImageToNewChat(item, {
      btn,
      onStatus: (text, isError) => this.flashStatus(text, Boolean(isError)),
    });
  }

  async saveItem(item) {
    if (!item?.url) return;
    try {
      await downloadMediaFile(item.url, item.filename);
      this.flashStatus('Сохранено');
    } catch (err) {
      this.flashStatus(err.message, true);
    }
  }

  openHashIfAny() {
    const m = window.location.hash.match(/^#upload-([0-9a-f-]+)$/i);
    if (!m) return;
    const idx = this.items.findIndex((it) => it.id === m[1]);
    if (idx >= 0) this.lightbox.open(idx);
  }

  async uploadFiles(fileList) {
    const fd = new FormData();
    for (const f of fileList) fd.append('files', f);
    this.flashStatus('Загрузка…');
    try {
      const res = await fetch('/api/gallery/uploads', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      const data = await res.json();
      this.flashStatus(`Загружено: ${data.count || 0}`);
      await this.refresh(false);
    } catch (err) {
      this.flashStatus(err.message || 'Ошибка', true);
    }
  }

  async extractMetadata(item) {
    if (!item?.id) return;
    try {
      const res = await fetch(`/api/gallery/uploads/${item.id}?extract=1`);
      if (!res.ok) throw new Error(res.statusText);
      const updated = await res.json();
      const idx = this.items.findIndex((it) => it.id === item.id);
      if (idx >= 0) {
        this.items[idx] = { ...this.items[idx], ...updated };
        this.itemById.set(item.id, this.items[idx]);
        this.renderGrid();
      }
      this.lightbox.setItems(this.items);
      this.lightbox.render();
      this.flashStatus('Метаданные обновлены');
    } catch (err) {
      this.flashStatus(err.message, true);
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new GalleryUploadsApp();
  app.init();
});
