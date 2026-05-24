/**
 * Параметры генерации img2img (denoise, CFG, число картинок) — UI и вставка в сообщение.
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'webchat_img2img_gen_preset_v1';
  const COLLAPSED_STORAGE_KEY = 'webchat_img2img_gen_preset_collapsed_v1';
  const IMG2IMG_SLUG = 'img2img';
  const SAVE_DEBOUNCE_MS = 280;

  const DEFAULTS = {
    enabled: true,
    denoiseMin: '',
    denoiseMax: '',
    cfgMin: '',
    cfgMax: '',
    count: '',
  };

  let saveTimer = null;

  function loadCollapsed() {
    try {
      return localStorage.getItem(COLLAPSED_STORAGE_KEY) !== '0';
    } catch {
      return true;
    }
  }

  function saveCollapsed(collapsed) {
    try {
      localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? '1' : '0');
    } catch { /* quota */ }
  }

  function clampNum(raw, min, max) {
    if (raw === '' || raw == null) return null;
    const n = Number.parseFloat(String(raw).replace(',', '.'));
    if (Number.isNaN(n)) return null;
    return Math.min(max, Math.max(min, n));
  }

  function clampInt(raw, min, max) {
    if (raw === '' || raw == null) return null;
    const n = Number.parseInt(String(raw), 10);
    if (Number.isNaN(n)) return null;
    return Math.min(max, Math.max(min, n));
  }

  function formatFixed(n, decimals) {
    return Number(n).toFixed(decimals);
  }

  /** Нормализация значения в поле ввода (сотые для denoise, десятые для CFG). */
  function normalizeFieldValue(raw, { min, max, decimals }) {
    const s = String(raw ?? '').trim().replace(',', '.');
    if (!s) return '';
    const n = clampNum(s, min, max);
    if (n == null) return s;
    return formatFixed(n, decimals);
  }

  function bindDecimalField(el, opts) {
    if (!el) return;
    const normalize = () => {
      const next = normalizeFieldValue(el.value, opts);
      if (next !== el.value) el.value = next;
    };
    el.addEventListener('blur', normalize);
    el.addEventListener('change', normalize);
  }

  function emitFieldChange(input) {
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function stepDecimalField(input, delta, opts) {
    if (!input) return;
    const raw = String(input.value ?? '').trim();
    let n = raw ? clampNum(raw.replace(',', '.'), opts.min, opts.max) : null;
    if (n == null && raw) return;
    if (n == null) n = opts.min;
    const factor = 10 ** opts.decimals;
    n += delta * opts.step;
    n = Math.round(n * factor) / factor;
    n = Math.min(opts.max, Math.max(opts.min, n));
    input.value = formatFixed(n, opts.decimals);
    emitFieldChange(input);
  }

  function stepCountField(input, delta) {
    if (!input) return;
    const min = 1;
    const max = 10;
    let n = clampInt(String(input.value ?? '').trim(), min, max);
    if (n == null) n = min;
    n = Math.min(max, Math.max(min, n + delta));
    input.value = String(n);
    emitFieldChange(input);
  }

  function bindStepper(input, opts) {
    const wrap = input?.closest('.img2img-gen-stepper');
    if (!wrap || !input) return;
    wrap.querySelectorAll('.img2img-gen-step').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const step = Number(btn.dataset.step);
        if (!Number.isFinite(step)) return;
        if (opts.kind === 'count') {
          stepCountField(input, step);
        } else {
          stepDecimalField(input, step, opts);
        }
      });
    });
  }

  function bindCountField(el) {
    if (!el) return;
    const normalize = () => {
      const raw = String(el.value ?? '').trim();
      if (!raw) return;
      const n = clampInt(raw, 1, 10);
      if (n != null) el.value = String(n);
    };
    el.addEventListener('blur', normalize);
    el.addEventListener('change', normalize);
  }

  /**
   * Диапазон для LLM: «denoising 0.50-0.60» — модель сама раскладывает по denoising_strengths.
   * decimals: 2 для denoise (0.50), 1 для CFG (4.0).
   */
  function formatRange(minRaw, maxRaw, { min, max, label, decimals = 2 }) {
    const minStr = String(minRaw ?? '').trim();
    const maxStr = String(maxRaw ?? '').trim();
    if (!minStr && !maxStr) return null;
    const lo = minStr ? clampNum(minStr, min, max) : null;
    const hi = maxStr ? clampNum(maxStr, min, max) : null;
    const fmt = (n) => formatFixed(n, decimals);
    if (lo != null && hi != null) {
      const a = Math.min(lo, hi);
      const b = Math.max(lo, hi);
      if (a === b) return `${label} ${fmt(a)}`;
      return `${label} ${fmt(a)}-${fmt(b)}`;
    }
    const single = lo ?? hi;
    if (single == null) return null;
    return `${label} ${fmt(single)}`;
  }

  function imageCountPhrase(count) {
    const n = clampInt(count, 1, 10);
    if (n == null) return null;
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) {
      return `Сделай ${n} изображение`;
    }
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) {
      return `Сделай ${n} изображения`;
    }
    return `Сделай ${n} изображений`;
  }

  function loadStored() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return { ...DEFAULTS };
      const data = JSON.parse(raw);
      return { ...DEFAULTS, ...data };
    } catch {
      return { ...DEFAULTS };
    }
  }

  function saveStored(values) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
    } catch { /* quota */ }
  }

  function isPanelEnabled(app) {
    const el = app.$.img2imgGenPresetEnabled;
    if (!el) return true;
    return el.checked;
  }

  /** Панель активна и вставка в промпт включена (пресет img2img). */
  function isInjectionEnabled(app) {
    return isImg2imgActive(app) && isPanelEnabled(app);
  }

  function syncFieldsDisabled(app) {
    const enabled = isPanelEnabled(app);
    const wrap = app.$.img2imgGenPresetFields;
    wrap?.classList.toggle('is-disabled', !enabled);
    const inputs = [
      app.$.img2imgDenoiseMin,
      app.$.img2imgDenoiseMax,
      app.$.img2imgCfgMin,
      app.$.img2imgCfgMax,
      app.$.img2imgCount,
    ].filter(Boolean);
    for (const el of inputs) {
      el.disabled = !enabled;
      el.setAttribute('aria-disabled', enabled ? 'false' : 'true');
    }
    wrap?.querySelectorAll('.img2img-gen-step').forEach((btn) => {
      btn.disabled = !enabled;
    });
  }

  function readFields(app) {
    const $ = app.$;
    const denoiseMinRaw = ($.img2imgDenoiseMin?.value ?? '').trim();
    const denoiseMaxRaw = ($.img2imgDenoiseMax?.value ?? '').trim();
    const cfgMinRaw = ($.img2imgCfgMin?.value ?? '').trim();
    const cfgMaxRaw = ($.img2imgCfgMax?.value ?? '').trim();
    return {
      enabled: isPanelEnabled(app),
      denoiseMin: denoiseMinRaw
        ? normalizeFieldValue(denoiseMinRaw, { min: 0, max: 1, decimals: 2 })
        : '',
      denoiseMax: denoiseMaxRaw
        ? normalizeFieldValue(denoiseMaxRaw, { min: 0, max: 1, decimals: 2 })
        : '',
      cfgMin: cfgMinRaw
        ? normalizeFieldValue(cfgMinRaw, { min: 1, max: 30, decimals: 1 })
        : '',
      cfgMax: cfgMaxRaw
        ? normalizeFieldValue(cfgMaxRaw, { min: 1, max: 30, decimals: 1 })
        : '',
      count: ($.img2imgCount?.value ?? '').trim(),
    };
  }

  function applyFields(app, values) {
    const $ = app.$;
    if ($.img2imgGenPresetEnabled) {
      $.img2imgGenPresetEnabled.checked = values.enabled !== false;
    }
    syncFieldsDisabled(app);
    if ($.img2imgDenoiseMin) {
      $.img2imgDenoiseMin.value = values.denoiseMin
        ? normalizeFieldValue(values.denoiseMin, { min: 0, max: 1, decimals: 2 })
        : '';
    }
    if ($.img2imgDenoiseMax) {
      $.img2imgDenoiseMax.value = values.denoiseMax
        ? normalizeFieldValue(values.denoiseMax, { min: 0, max: 1, decimals: 2 })
        : '';
    }
    if ($.img2imgCfgMin) {
      $.img2imgCfgMin.value = values.cfgMin
        ? normalizeFieldValue(values.cfgMin, { min: 1, max: 30, decimals: 1 })
        : '';
    }
    if ($.img2imgCfgMax) {
      $.img2imgCfgMax.value = values.cfgMax
        ? normalizeFieldValue(values.cfgMax, { min: 1, max: 30, decimals: 1 })
        : '';
    }
    if ($.img2imgCount) $.img2imgCount.value = values.count ?? '';
  }

  function scheduleSave(app) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveStored(readFields(app));
    }, SAVE_DEBOUNCE_MS);
  }

  function buildInstruction(app) {
    if (!isInjectionEnabled(app)) return '';
    const v = readFields(app);
    const parts = [];
    const denoise = formatRange(v.denoiseMin, v.denoiseMax, {
      min: 0,
      max: 1,
      label: 'denoising',
      decimals: 2,
    });
    if (denoise) parts.push(denoise);
    const cfg = formatRange(v.cfgMin, v.cfgMax, {
      min: 1,
      max: 30,
      label: 'CFG',
      decimals: 1,
    });
    if (cfg) parts.push(cfg);
    if (v.count) {
      const images = imageCountPhrase(v.count);
      if (images) parts.push(images);
    }
    if (!parts.length) return '';
    return `${parts.join('; ')}.`;
  }

  /** Одна часть инструкции img2img (denoising / CFG / число картинок). */
  const INSTRUCTION_PART_RE =
    /^(?:denoising\s+[\d.]+(?:-[\d.]+)?|CFG\s+[\d.]+(?:-[\d.]+)?|Сделай\s+\d+\s+изображен\w*)\.?$/i;

  function isInstructionBlock(block) {
    const normalized = String(block || '').trim().replace(/\.\s*$/, '');
    if (!normalized) return false;
    const parts = normalized.split(';').map((p) => p.trim()).filter(Boolean);
    return parts.length > 0 && parts.every((p) => INSTRUCTION_PART_RE.test(p));
  }

  /** Убрать скрытый префикс из текста, сохранённого на сервере. */
  function stripFromStoredMessage(text) {
    const raw = text || '';
    const trimmed = raw.trim();
    if (!trimmed) return '';
    const sep = trimmed.indexOf('\n\n');
    if (sep === -1) {
      return isInstructionBlock(trimmed) ? '' : raw;
    }
    const head = trimmed.slice(0, sep).trim();
    const rest = trimmed.slice(sep + 2);
    return isInstructionBlock(head) ? rest : raw;
  }

  /** Текст для отправки в LLM (с подсказками из панели img2img, если включено). */
  function getPayloadText(app, rawText) {
    if (!isInjectionEnabled(app)) return (rawText || '').trim();
    const hint = buildInstruction(app);
    const body = (rawText || '').trim();
    if (!hint) return body;
    if (!body) return hint;
    return `${hint}\n\n${body}`;
  }

  function augmentMessage(app, text) {
    return getPayloadText(app, text);
  }

  function activePresetSlug(app) {
    const presetId = app.$.chatPresetSelect?.value || app.currentConv?.preset_id;
    if (!presetId || !app.presets?.length) return null;
    return app.presets.find((p) => p.id === presetId)?.slug ?? null;
  }

  function isImg2imgActive(app) {
    return activePresetSlug(app) === IMG2IMG_SLUG;
  }

  function setExpanded(app, expanded) {
    const panel = app.$.img2imgGenPresetPanel;
    const toggle = app.$.img2imgGenPresetToggle;
    if (!panel || !toggle) return;
    panel.classList.toggle('hidden', !expanded);
    toggle.classList.toggle('is-active', expanded);
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }

  function syncCollapseState(app) {
    if (!isImg2imgActive(app)) return;
    setExpanded(app, !loadCollapsed());
  }

  function togglePanel(app) {
    const toggle = app.$.img2imgGenPresetToggle;
    if (!toggle || toggle.classList.contains('hidden')) return;
    const expanded = toggle.getAttribute('aria-expanded') !== 'true';
    setExpanded(app, expanded);
    saveCollapsed(!expanded);
  }

  function syncVisibility(app) {
    const show = isImg2imgActive(app)
      && Boolean(app.currentConvId)
      && !app.$.chatHistory?.classList.contains('hidden');
    const toggle = app.$.img2imgGenPresetToggle;
    const panel = app.$.img2imgGenPresetPanel;
    if (!toggle || !panel) return;

    toggle.classList.toggle('hidden', !show);
    app.$.chatPresetToolbar?.classList.toggle('has-img2img-preset', show);

    if (!show) {
      panel.classList.add('hidden');
      toggle.classList.remove('is-active');
      toggle.setAttribute('aria-expanded', 'false');
      return;
    }
    syncCollapseState(app);
  }

  function bind(app) {
    applyFields(app, loadStored());
    const inputs = [
      app.$.img2imgDenoiseMin,
      app.$.img2imgDenoiseMax,
      app.$.img2imgCfgMin,
      app.$.img2imgCfgMax,
      app.$.img2imgCount,
    ].filter(Boolean);
    for (const el of inputs) {
      el.addEventListener('input', () => scheduleSave(app));
      el.addEventListener('change', () => saveStored(readFields(app)));
    }
    const denoiseOpts = { min: 0, max: 1, decimals: 2, step: 0.01 };
    const cfgOpts = { min: 1, max: 30, decimals: 1, step: 0.1 };
    bindDecimalField(app.$.img2imgDenoiseMin, denoiseOpts);
    bindDecimalField(app.$.img2imgDenoiseMax, denoiseOpts);
    bindDecimalField(app.$.img2imgCfgMin, cfgOpts);
    bindDecimalField(app.$.img2imgCfgMax, cfgOpts);
    bindCountField(app.$.img2imgCount);
    bindStepper(app.$.img2imgDenoiseMin, denoiseOpts);
    bindStepper(app.$.img2imgDenoiseMax, denoiseOpts);
    bindStepper(app.$.img2imgCfgMin, cfgOpts);
    bindStepper(app.$.img2imgCfgMax, cfgOpts);
    bindStepper(app.$.img2imgCount, { kind: 'count' });
    app.$.img2imgGenPresetEnabled?.addEventListener('change', () => {
      syncFieldsDisabled(app);
      saveStored(readFields(app));
    });
    app.$.img2imgGenPresetToggle?.addEventListener('click', () => togglePanel(app));
    syncFieldsDisabled(app);
    syncVisibility(app);
  }

  window.WebChatImg2imgPreset = {
    STORAGE_KEY,
    init: bind,
    syncVisibility,
    buildInstruction,
    getPayloadText,
    augmentMessage,
    stripFromStoredMessage,
    isImg2imgActive,
    isInjectionEnabled,
    isPanelEnabled,
  };
})();
