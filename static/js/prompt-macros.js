/**
 * Быстрые промпты (@alias): picker, автодополнение, спойлеры в чате.
 * Редактирование — на странице /macros.
 */
/* global ChatApp */

const PROMPT_MACRO_CATEGORIES = [
  { id: 'character', label: 'Персонажи' },
  { id: 'environment', label: 'Окружение' },
  { id: 'situation', label: 'Ситуации' },
  { id: 'other', label: 'Прочее' },
];

/** @alias или @@alias (второй @ — экранирование, в UI один @). */
const MACRO_MENTION_RE = /@?@([a-zA-Z0-9_-]+)/g;

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function escapeAttr(s) {
  return String(s ?? '').replace(/"/g, '&quot;');
}

class PromptMacrosUI {
  constructor(app) {
    this.app = app;
    this.macros = [];
    this.byAlias = new Map();
    this.autocompleteIndex = -1;
    this.autocompleteMatches = [];
  }

  async load() {
    try {
      this.macros = await this.app.api('/api/prompt-macros');
      this.byAlias = new Map(this.macros.map((m) => [m.alias.toLowerCase(), m]));
    } catch (err) {
      this.app.log?.warn('macros', err.message);
      this.macros = [];
      this.byAlias = new Map();
    }
    return this.macros;
  }

  macroByAlias(alias) {
    return this.byAlias.get(String(alias).toLowerCase()) ?? null;
  }

  filterByPrefix(prefix) {
    const q = prefix.toLowerCase();
    return this.macros
      .filter((m) => m.alias.startsWith(q) || (m.label && m.label.toLowerCase().includes(q)))
      .slice(0, 12);
  }

  renderUserText(container, text) {
    container.innerHTML = '';
    if (!text) return;

    const re = new RegExp(MACRO_MENTION_RE.source, 'g');
    let last = 0;
    let match;
    while ((match = re.exec(text)) !== null) {
      const alias = match[1];
      const macro = this.macroByAlias(alias);
      const spanStart = match.index;
      const spanEnd = match.index + match[0].length;
      if (spanStart > last) {
        container.appendChild(document.createTextNode(text.slice(last, spanStart)));
      }
      if (macro) {
        container.appendChild(this._createMentionSpoiler(macro));
      } else {
        container.appendChild(document.createTextNode(text.slice(spanStart, spanEnd)));
      }
      last = spanEnd;
    }
    if (last < text.length) {
      container.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  _createMentionSpoiler(macro) {
    const details = document.createElement('details');
    details.className = 'mention-spoiler';
    const summary = document.createElement('summary');
    summary.className = 'mention-spoiler-summary';
    const label = macro.label ? `${macro.label}` : `@${macro.alias}`;
    // Символ @ рисует CSS (.mention-spoiler-summary::before)
    summary.textContent = macro.alias;
    summary.title = label;
    const body = document.createElement('div');
    body.className = 'mention-spoiler-body';
    body.textContent = macro.body;
    details.appendChild(summary);
    details.appendChild(body);
    return details;
  }

  /** @returns {boolean} */
  isAutocompleteOpen() {
    const list = document.getElementById('macro-mention-list');
    return Boolean(list && !list.classList.contains('hidden') && this.autocompleteMatches.length > 0);
  }

  applyAutocompleteSelection() {
    const textarea = this.app.$.userInput;
    const list = document.getElementById('macro-mention-list');
    if (!textarea || !list || list.classList.contains('hidden')) return false;
    const m = this.autocompleteMatches[this.autocompleteIndex];
    if (!m) return false;
    const pos = textarea.selectionStart ?? 0;
    const at = textarea.value.slice(0, pos).lastIndexOf('@');
    if (at < 0) return false;
    this._applyAutocomplete(textarea, at, m.alias);
    list.classList.add('hidden');
    list.innerHTML = '';
    this.autocompleteIndex = -1;
    this.autocompleteMatches = [];
    return true;
  }

  isPickerOpen() {
    const pop = document.getElementById('macro-picker-popover');
    return Boolean(pop && !pop.classList.contains('hidden'));
  }

  _macroSnippetBefore(textBeforeCursor, alias) {
    const trimmed = textBeforeCursor.replace(/\s+$/, '');
    const needsAt = !trimmed.endsWith('@');
    return `${needsAt ? '@' : ''}${alias} `;
  }

  insertMacroAtCursor(textarea, alias) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    const snippet = this._macroSnippetBefore(before, alias);
    textarea.value = before + snippet + after;
    const pos = start + snippet.length;
    textarea.selectionStart = textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
    this.app.autoResizeInput?.();
  }

  insertAtCursor(textarea, snippet) {
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? start;
    const before = textarea.value.slice(0, start);
    const after = textarea.value.slice(end);
    textarea.value = before + snippet + after;
    const pos = start + snippet.length;
    textarea.selectionStart = textarea.selectionEnd = pos;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
    this.app.autoResizeInput?.();
  }

  bindInputAutocomplete(textarea) {
    if (!textarea || textarea.dataset.macroAutocomplete) return;
    textarea.dataset.macroAutocomplete = '1';

    const list = document.getElementById('macro-mention-list');
    if (!list) return;

    if (!list.dataset.macroListBound) {
      list.dataset.macroListBound = '1';
      list.addEventListener('mousedown', (e) => e.preventDefault());
    }

    const hide = () => {
      list.classList.add('hidden');
      list.innerHTML = '';
      this.autocompleteIndex = -1;
      this.autocompleteMatches = [];
    };

    const renderList = () => {
      const value = textarea.value;
      const pos = textarea.selectionStart ?? value.length;
      const before = value.slice(0, pos);
      const at = before.lastIndexOf('@');
      if (at < 0) {
        hide();
        return;
      }
      const chunk = before.slice(at + 1);
      if (/\s/.test(chunk)) {
        hide();
        return;
      }
      this.autocompleteMatches = this.filterByPrefix(chunk);
      if (!this.autocompleteMatches.length) {
        hide();
        return;
      }
      this.autocompleteIndex = 0;
      list.classList.remove('hidden');
      list.innerHTML = this.autocompleteMatches
        .map(
          (m, i) => `<button type="button" class="macro-mention-item${i === 0 ? ' active' : ''}" data-alias="${escapeAttr(m.alias)}">
            <span class="macro-mention-alias">@${escapeHtml(m.alias)}</span>
            <span class="macro-mention-label">${escapeHtml(m.label || m.category_label)}</span>
          </button>`,
        )
        .join('');
      list.querySelectorAll('.macro-mention-item').forEach((btn) => {
        btn.addEventListener('mousedown', (e) => {
          e.preventDefault();
          this._applyAutocomplete(textarea, at, btn.dataset.alias);
          hide();
        });
      });
    };

    textarea.addEventListener('input', renderList);
    textarea.addEventListener('keydown', (e) => {
      if (list.classList.contains('hidden')) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.autocompleteIndex = Math.min(
          this.autocompleteIndex + 1,
          this.autocompleteMatches.length - 1,
        );
        this._highlightAutocompleteItem(list);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.autocompleteIndex = Math.max(this.autocompleteIndex - 1, 0);
        this._highlightAutocompleteItem(list);
      } else if (e.key === 'Enter' && this.autocompleteIndex >= 0) {
        e.preventDefault();
        e.stopPropagation();
        this.applyAutocompleteSelection();
      } else if (e.key === 'Escape') {
        hide();
      }
    });
    textarea.addEventListener('blur', () => setTimeout(hide, 150));
  }

  _highlightAutocompleteItem(list) {
    list.querySelectorAll('.macro-mention-item').forEach((el, i) => {
      el.classList.toggle('active', i === this.autocompleteIndex);
    });
  }

  _applyAutocomplete(textarea, atIndex, alias) {
    const pos = textarea.selectionStart ?? textarea.value.length;
    let replaceFrom = atIndex;
    if (replaceFrom > 0 && textarea.value[replaceFrom - 1] === '@') {
      replaceFrom -= 1;
    }
    const before = textarea.value.slice(0, replaceFrom);
    const after = textarea.value.slice(pos);
    const snippet = this._macroSnippetBefore(before, alias);
    textarea.value = before + snippet + after;
    const cursor = before.length + snippet.length;
    textarea.selectionStart = textarea.selectionEnd = cursor;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    this.app.autoResizeInput?.();
  }

  openPicker() {
    const pop = document.getElementById('macro-picker-popover');
    if (!pop) return;
    if (!pop.dataset.pickerBound) {
      pop.dataset.pickerBound = '1';
      pop.addEventListener('mousedown', (e) => e.stopPropagation());
      pop.addEventListener('click', (e) => e.stopPropagation());
    }
    this.renderPicker(pop);
    pop.classList.remove('hidden');
  }

  closePicker() {
    document.getElementById('macro-picker-popover')?.classList.add('hidden');
  }

  renderPicker(container) {
    const tabs = PROMPT_MACRO_CATEGORIES.map(
      (c) => `<button type="button" class="macro-picker-tab${c.id === this.pickerCategory ? ' active' : ''}" data-cat="${c.id}">${escapeHtml(c.label)}</button>`,
    ).join('');
    const cat = this.pickerCategory || 'character';
    const items = this.macros
      .filter((m) => m.category === cat)
      .map(
        (m) => `<button type="button" class="macro-picker-item" data-alias="${escapeAttr(m.alias)}">
          <span class="macro-picker-item-alias">@${escapeHtml(m.alias)}</span>
          <span class="macro-picker-item-hint">${escapeHtml(m.label || m.body.slice(0, 60))}</span>
        </button>`,
      )
      .join('');
    container.innerHTML = `
      <div class="macro-picker-header">
        <span>Быстрые промпты</span>
        <button type="button" class="macro-picker-close icon-btn small" aria-label="Закрыть">✕</button>
      </div>
      <div class="macro-picker-tabs">${tabs}</div>
      <div class="macro-picker-list">${items || '<p class="macro-picker-empty">Нет записей в категории</p>'}</div>`;

    if (!this.pickerCategory) this.pickerCategory = 'character';

    container.querySelector('.macro-picker-close')?.addEventListener('click', (e) => {
      e.stopPropagation();
      this.closePicker();
    });
    container.querySelectorAll('.macro-picker-tab').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.pickerCategory = btn.dataset.cat;
        this.renderPicker(container);
      });
    });
    container.querySelectorAll('.macro-picker-item').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const ta = this.app.$.userInput;
        if (ta) this.insertMacroAtCursor(ta, btn.dataset.alias);
        this.closePicker();
      });
    });
  }

}

window.PromptMacrosUI = PromptMacrosUI;
