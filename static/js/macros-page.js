/**
 * Страница быстрых промптов: таблица по категориям (листы), inline-редактирование.
 */

const SAVE_ICONS = `<svg class="macro-cell-save-icon macro-cell-save-icon--disk" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg><svg class="macro-cell-save-icon macro-cell-save-icon--check" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`;

const $ = (sel, root = document) => root.querySelector(sel);

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function escapeAttr(s) {
  return String(s ?? '').replace(/"/g, '&quot;');
}

let rowKeySeq = 0;

function newRowKey() {
  rowKeySeq += 1;
  return `new-${rowKeySeq}`;
}

class MacrosPageApp {
  constructor() {
    this.macros = [];
    this.activeCategory = 'character';
    this.drafts = new Map();
    this.els = {
      tbody: $('#macros-table-body'),
      empty: $('#macros-sheet-empty'),
      count: $('#macros-total-count'),
      status: $('#macros-page-status'),
      addRow: $('#macros-add-row'),
    };
  }

  async init() {
    this.bindSheetTabs();
    this.els.addRow?.addEventListener('click', () => this.addDraftRow());
    await this.load();
    this.renderTable();
  }

  bindSheetTabs() {
    document.querySelectorAll('.macros-sheet-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.activeCategory = btn.dataset.category;
        document.querySelectorAll('.macros-sheet-tab').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        this.renderTable();
      });
    });
  }

  async api(path, options = {}) {
    const res = await fetch(path, {
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = await res.json();
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async load() {
    this.macros = await this.api('/api/prompt-macros');
    this.drafts = new Map();
    this.els.count.textContent = String(this.macros.length);
  }

  rowsForActiveSheet() {
    const saved = this.macros.filter((m) => m.category === this.activeCategory);
    const drafts = [...this.drafts.values()].filter((d) => d.category === this.activeCategory && !d.id);
    return { saved, drafts };
  }

  addDraftRow() {
    const key = newRowKey();
    this.drafts.set(key, {
      rowKey: key,
      id: null,
      category: this.activeCategory,
      alias: '',
      label: '',
      body: '',
      sort_order: 0,
      dirty: true,
      isNew: true,
    });
    this.renderTable();
    const row = this.els.tbody?.querySelector(`tr[data-row-key="${key}"]`);
    row?.querySelector('[data-field="alias"]')?.focus();
  }

  renderTable() {
    const { saved, drafts } = this.rowsForActiveSheet();
    const rows = [
      ...saved.map((m) => ({ ...m, rowKey: m.id, isNew: false })),
      ...drafts,
    ].sort(
      (a, b) =>
        (a.sort_order ?? 0) - (b.sort_order ?? 0) ||
        String(a.alias).localeCompare(String(b.alias)),
    );

    if (!rows.length) {
      this.els.tbody.innerHTML = '';
      this.els.empty?.classList.remove('hidden');
      return;
    }
    this.els.empty?.classList.add('hidden');
    this.els.tbody.innerHTML = rows.map((r) => this.renderRow(r)).join('');
    this.els.tbody.querySelectorAll('tr').forEach((tr) => this.bindRow(tr));
  }

  cellField(rowKey, field, value, multiline, withSave = false) {
    const grow = multiline ? ' macro-cell-input--grow' : '';
    const inner = multiline
      ? `<textarea class="macro-cell-input${grow}" data-field="${field}" rows="1">${escapeHtml(value)}</textarea>`
      : field === 'sort_order'
        ? `<input class="macro-cell-input macro-cell-input--order" data-field="${field}" type="number" min="0" max="9999" value="${escapeAttr(value)}">`
        : `<input class="macro-cell-input" data-field="${field}" type="text" value="${escapeAttr(value)}">`;
    const saveBtn = withSave
      ? `<button type="button" class="macro-cell-save" title="Сохранить строку" aria-label="Сохранить">${SAVE_ICONS}</button>`
      : '';
    return `<div class="macro-cell-field${multiline ? ' macro-cell-field--grow' : ''}${withSave ? ' macro-cell-field--save' : ''}">${inner}${saveBtn}</div>`;
  }

  renderRow(r) {
    const key = r.rowKey;
    const dirty = r.dirty || r.isNew;
    return `<tr class="macros-table-row${dirty ? ' is-dirty' : ''}${r.isNew ? ' is-new' : ''}" data-row-key="${escapeAttr(key)}" data-id="${escapeAttr(r.id || '')}">
      <td class="col-alias">${this.cellField(key, 'alias', r.alias, true)}</td>
      <td class="col-label">${this.cellField(key, 'label', r.label || '', true)}</td>
      <td class="col-body">${this.cellField(key, 'body', r.body, true, true)}</td>
      <td class="col-order">${this.cellField(key, 'sort_order', String(r.sort_order ?? 0), false)}</td>
      <td class="col-actions">
        <button type="button" class="macro-row-delete" title="Удалить" aria-label="Удалить">×</button>
      </td>
    </tr>`;
  }

  bindRow(tr) {
    const rowKey = tr.dataset.rowKey;
    tr.querySelectorAll('.macro-cell-input').forEach((el) => {
      this.autoGrow(el);
      el.addEventListener('input', () => {
        this.onFieldInput(rowKey);
        this.autoGrow(el);
      });
    });
    tr.querySelector('.macro-cell-save')?.addEventListener('click', (e) => {
      void this.saveRow(rowKey, e.currentTarget);
    });
    tr.querySelector('.macro-row-delete')?.addEventListener('click', () => {
      void this.deleteRow(rowKey);
    });
  }

  onFieldInput(rowKey) {
    const tr = this.els.tbody?.querySelector(`tr[data-row-key="${rowKey}"]`);
    if (!tr) return;
    tr.classList.add('is-dirty');
    const id = tr.dataset.id || null;
    const data = this.readRowData(rowKey);
    if (!data) return;
    if (id) {
      this.drafts.set(rowKey, { ...data, rowKey, id, dirty: true });
    } else {
      this.drafts.set(rowKey, { ...data, rowKey, id: null, isNew: true, dirty: true });
    }
  }

  autoGrow(el) {
    if (!el.classList.contains('macro-cell-input--grow')) return;
    el.style.height = 'auto';
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const pad = 10;
    const maxH = lineHeight * 7 + pad;
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
    el.style.overflowY = el.scrollHeight > maxH ? 'auto' : 'hidden';
  }

  readRowData(rowKey) {
    const tr = this.els.tbody?.querySelector(`tr[data-row-key="${rowKey}"]`);
    if (!tr) return null;
    return {
      category: this.activeCategory,
      alias: tr.querySelector('[data-field="alias"]')?.value?.trim() || '',
      label: tr.querySelector('[data-field="label"]')?.value?.trim() || null,
      body: tr.querySelector('[data-field="body"]')?.value?.trim() || '',
      sort_order: Number(tr.querySelector('[data-field="sort_order"]')?.value) || 0,
    };
  }

  flashSaveBtn(btn, ok) {
    if (!btn) return;
    btn.classList.remove('is-saving', 'is-success', 'is-error');
    if (ok) {
      btn.classList.add('is-success');
      setTimeout(() => btn.classList.remove('is-success'), 1400);
    } else {
      btn.classList.add('is-error');
      setTimeout(() => btn.classList.remove('is-error'), 2000);
    }
  }

  setStatus(msg, kind = '') {
    const el = this.els.status;
    if (!el) return;
    el.textContent = msg;
    el.className = 'macros-page-status';
    if (kind) el.classList.add(`is-${kind}`, 'is-visible');
    if (kind === 'ok') {
      setTimeout(() => {
        el.classList.remove('is-visible');
        el.textContent = '';
      }, 2500);
    }
  }

  async saveRow(rowKey, saveBtn) {
    const tr = this.els.tbody?.querySelector(`tr[data-row-key="${rowKey}"]`);
    const payload = this.readRowData(rowKey);
    if (!payload?.alias || !payload.body) {
      this.setStatus('Укажите alias и текст промпта', 'error');
      this.flashSaveBtn(saveBtn, false);
      return;
    }

    saveBtn?.classList.add('is-saving');
    try {
      const id = tr?.dataset.id;
      let saved;
      if (id) {
        saved = await this.api(`/api/prompt-macros/${id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        saved = await this.api('/api/prompt-macros', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
      this.drafts.delete(rowKey);
      const idx = this.macros.findIndex((m) => m.id === saved.id);
      if (idx >= 0) this.macros[idx] = saved;
      else this.macros.push(saved);
      this.els.count.textContent = String(this.macros.length);
      this.renderTable();
      this.setStatus(id ? 'Сохранено' : 'Создано', 'ok');
      const newBtn = this.els.tbody?.querySelector(`tr[data-id="${saved.id}"] .macro-cell-save`);
      this.flashSaveBtn(newBtn || saveBtn, true);
    } catch (err) {
      this.setStatus(err.message, 'error');
      this.flashSaveBtn(saveBtn, false);
    } finally {
      saveBtn?.classList.remove('is-saving');
    }
  }

  async deleteRow(rowKey) {
    const tr = this.els.tbody?.querySelector(`tr[data-row-key="${rowKey}"]`);
    const id = tr?.dataset.id;
    if (!id) {
      this.drafts.delete(rowKey);
      this.renderTable();
      return;
    }
    if (!window.confirm('Удалить эту запись?')) return;
    try {
      await this.api(`/api/prompt-macros/${id}`, { method: 'DELETE' });
      this.macros = this.macros.filter((m) => m.id !== id);
      this.drafts.delete(rowKey);
      this.els.count.textContent = String(this.macros.length);
      this.renderTable();
      this.setStatus('Удалено', 'ok');
    } catch (err) {
      this.setStatus(err.message, 'error');
    }
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new MacrosPageApp();
  app.init().catch((err) => {
    const st = $('#macros-page-status');
    if (st) {
      st.textContent = err.message;
      st.classList.add('is-error', 'is-visible');
    }
  });
});
