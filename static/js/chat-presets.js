/**
 * Пресеты: загрузка, черновики промптов, селекты (P5.4).
 * Подключается до chat.js; API: WebChatPresets.*
 */
(function () {
  'use strict';

  const DRAFTS_KEY = 'webchat_preset_drafts_v1';
  const LAST_EDIT_KEY = 'webchat_preset_last_edit_id';
  const LAST_CHAT_PRESET_KEY = 'webchat_last_chat_preset_id';
  const SHORT_LABELS = {
    default: 'Default',
    image_gen: 'txt2img',
    img2img: 'img2img',
    document_analysis: 'Docs',
  };

  function readDrafts(app) {
    try {
      const raw = localStorage.getItem(DRAFTS_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  function writeDrafts(app, drafts) {
    try {
      localStorage.setItem(DRAFTS_KEY, JSON.stringify(drafts));
    } catch (err) {
      app.log?.warn('settings', `Не удалось записать черновики пресетов: ${err.message}`);
    }
  }

  function flushDraftsToStorage(app) {
    const presetId = app._editingPresetId || app.$.presetSelect?.value;
    if (presetId && app.$.presetSystemPrompt) {
      writeDraft(app, presetId, app.$.presetSystemPrompt.value, false);
    }
  }

  function writeDraft(app, presetId, text, synced = false) {
    if (!presetId) return;
    const drafts = readDrafts();
    drafts[presetId] = { text, synced, updatedAt: Date.now() };
    writeDrafts(app, drafts);
  }

  function markDraftSynced(app, presetId, text) {
    writeDraft(app, presetId, text, true);
  }

  function hasUnsyncedDrafts(app) {
    const drafts = readDrafts();
    return Object.values(drafts).some((d) => d && d.synced === false);
  }

  function getPromptText(app, presetId) {
    const preset = app.presets.find((p) => p.id === presetId);
    const serverText = preset?.system_prompt ?? '';
    const draft = readDrafts()[presetId];
    if (draft && draft.synced === false && typeof draft.text === 'string') {
      return draft.text;
    }
    return serverText;
  }

  function promptDiffers(app, presetId, text) {
    const preset = app.presets.find((p) => p.id === presetId);
    return (preset?.system_prompt ?? '') !== text;
  }

  function isPromptDirty(app, presetId = app._editingPresetId || app.$.presetSelect?.value) {
    if (!presetId || !app.$.presetSystemPrompt) return false;
    return promptDiffers(app, presetId, app.$.presetSystemPrompt.value);
  }

  function onPromptInput(app) {
    const presetId = app._editingPresetId || app.$.presetSelect?.value;
    if (!presetId) return;
    clearTimeout(app._presetDraftDebounceTimer);
    app._presetDraftDebounceTimer = setTimeout(() => {
      writeDraft(app, presetId, app.$.presetSystemPrompt.value, false);
    }, 280);
  }

  async function load(app) {
    app.presets = await app.api('/api/presets');
    mergeUnsyncedDrafts(app);
    await syncPendingDrafts(app);
    const optionsHtml = app.presets
      .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`)
      .join('');
    populateGlobalSelect(app);
    populateConvSelect(app, app.currentConv?.preset_id);
    WebChatImg2imgPreset?.refreshPresetCache?.(app);
    WebChatImg2imgPreset?.logDiagnostics?.(app, 'presets_loaded');
  }

  function mergeUnsyncedDrafts(app) {
    const drafts = readDrafts();
    for (const preset of app.presets) {
      const draft = drafts[preset.id];
      if (draft && draft.synced === false && typeof draft.text === 'string') {
        preset.system_prompt = draft.text;
      }
    }
  }

  async function syncPendingDrafts(app) {
    const drafts = readDrafts();
    for (const preset of app.presets) {
      const draft = drafts[preset.id];
      if (!draft || draft.synced !== false || typeof draft.text !== 'string') continue;
      if (!promptDiffers(app, preset.id, draft.text)) {
        markDraftSynced(app, preset.id, draft.text);
        continue;
      }
      try {
        await savePromptForId(app, preset.id, draft.text, { silent: true });
      } catch {
        /* черновик остаётся в localStorage */
      }
    }
  }

  function populateGlobalSelect(app) {
    if (!app.$.presetSelect || app.presets.length === 0) return;
    const stored = localStorage.getItem(LAST_EDIT_KEY);
    const fallback = app.presets.find((p) => p.is_default)?.id ?? app.presets[0].id;
    const activeId = (stored && app.presets.some((p) => p.id === stored))
      ? stored
      : fallback;
    app.$.presetSelect.innerHTML = app.presets
      .map((p) => `<option value="${p.id}"${p.id === activeId ? ' selected' : ''}>${escapeHtml(p.name)}</option>`)
      .join('');
    app.$.presetSelect.disabled = false;
    if (app.$.presetSystemPrompt) app.$.presetSystemPrompt.disabled = false;
    app._editingPresetId = activeId;
    localStorage.setItem(LAST_EDIT_KEY, activeId);
    syncPromptField(app);
    updateDefaultButton(app);
  }

  function chatShortLabel(app, preset) {
    return SHORT_LABELS[preset.slug] ?? preset.name;
  }

  function rememberLastChatPreset(app, presetId) {
    if (!presetId || !app.presets.some((p) => p.id === presetId)) return;
    try {
      localStorage.setItem(LAST_CHAT_PRESET_KEY, presetId);
    } catch (err) {
      app.log?.warn('settings', `Не удалось сохранить последний пресет чата: ${err.message}`);
    }
  }

  function getLastUsedPresetId(app) {
    const fromSelect = app.$.chatPresetSelect?.value;
    if (fromSelect && app.presets.some((p) => p.id === fromSelect)) return fromSelect;
    const fromConv = app.currentConv?.preset_id;
    if (fromConv && app.presets.some((p) => p.id === fromConv)) return fromConv;
    try {
      const stored = localStorage.getItem(LAST_CHAT_PRESET_KEY);
      if (stored && app.presets.some((p) => p.id === stored)) return stored;
    } catch {
      /* ignore */
    }
    const def = app.presets.find((p) => p.is_default);
    return def?.id ?? app.presets[0]?.id ?? null;
  }

  function populateConvSelect(app, selectedId) {
    if (app.presets.length === 0) return;
    const fallback = app.presets.find((p) => p.is_default)?.id ?? app.presets[0].id;
    const activeId = selectedId ?? fallback;
    const activeKey = activeId != null ? String(activeId).trim().toLowerCase() : '';
    const optionAttrs = (p) => {
      const selected = String(p.id).trim().toLowerCase() === activeKey;
      return `value="${p.id}"${selected ? ' selected' : ''}`;
    };
    const optionsHtml = app.presets
      .map((p) => `<option ${optionAttrs(p)}>${escapeHtml(p.name)}</option>`)
      .join('');
    const chatOptionsHtml = app.presets
      .map((p) => `<option ${optionAttrs(p)}>${escapeHtml(chatShortLabel(app, p))}</option>`)
      .join('');
    const disabled = !app.currentConvId;
    if (app.$.convPresetSelect) {
      app.$.convPresetSelect.innerHTML = optionsHtml;
      app.$.convPresetSelect.disabled = disabled;
    }
    if (app.$.chatPresetSelect) {
      app.$.chatPresetSelect.innerHTML = chatOptionsHtml;
      app.$.chatPresetSelect.disabled = disabled;
      app.$.chatPresetSelect.title = 'Пресет для следующего сообщения';
      if (activeId != null) {
        app.$.chatPresetSelect.value = String(activeId);
      }
    }
    if (app.$.convPresetSelect && activeId != null) {
      app.$.convPresetSelect.value = String(activeId);
    }
    if (activeId != null) rememberLastChatPreset(app, activeId);
    updateChatToolbar(app);
  }

  function updateChatToolbar(app) {
    const show = Boolean(app.currentConvId) && !app.$.chatHistory?.classList.contains('hidden');
    app.$.chatPresetToolbar?.classList.toggle('hidden', !show);
    WebChatImg2imgPreset?.syncVisibility?.(app);
  }

  async function onChatPresetChange(app) {
    const presetId = app.$.chatPresetSelect?.value;
    if (!presetId || !app.currentConvId) return;
    rememberLastChatPreset(app, presetId);
    if (app.$.convPresetSelect) app.$.convPresetSelect.value = presetId;
    WebChatImg2imgPreset?.syncVisibility?.(app);
    await applyConversationPreset(app, presetId);
    WebChatImg2imgPreset?.syncVisibility?.(app);
  }

  async function applyConversationPreset(app, presetId) {
    if (!app.currentConvId) return;
    if (String(app.currentConv?.preset_id) === String(presetId)) {
      WebChatImg2imgPreset?.syncVisibility?.(app);
      return;
    }
    try {
      app.currentConv = await app.api(`/api/conversations/${app.currentConvId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset_id: presetId }),
      });
      if (app.$.convPresetSelect) app.$.convPresetSelect.value = presetId;
      if (app.$.chatPresetSelect) app.$.chatPresetSelect.value = presetId;
      WebChatImg2imgPreset?.logDiagnostics?.(app, 'preset_patched', { presetId });
    } catch (err) {
      app.showError(err.message || 'Не удалось сменить пресет');
      populateConvSelect(app, app.currentConv?.preset_id);
    } finally {
      WebChatImg2imgPreset?.syncVisibility?.(app);
    }
  }

  function syncPromptField(app) {
    if (!app.$.presetSelect || !app.$.presetSystemPrompt) return;
    const presetId = app.$.presetSelect.value;
    if (!presetId) {
      app.$.presetSystemPrompt.value = '';
      app._editingPresetId = null;
      return;
    }
    app._editingPresetId = presetId;
    app.$.presetSystemPrompt.value = getPromptText(app, presetId);
    resetPromptSaveBtn(app);
    updateDefaultButton(app);
  }

  function updateDefaultButton(app) {
    const btn = app.$.presetSetDefaultBtn;
    const presetId = app.$.presetSelect?.value;
    if (!btn || !presetId) {
      if (btn) btn.disabled = true;
      return;
    }
    const preset = app.presets.find((p) => p.id === presetId);
    btn.disabled = Boolean(preset?.is_default);
    btn.textContent = preset?.is_default
      ? 'Пресет по умолчанию'
      : 'Сделать пресетом по умолчанию';
  }

  async function onSelectChange(app) {
    const oldId = app._editingPresetId;
    const newId = app.$.presetSelect?.value;
    if (oldId && oldId !== newId && app.$.presetSystemPrompt) {
      const text = app.$.presetSystemPrompt.value;
      writeDraft(app, oldId, text, false);
      if (promptDiffers(app, oldId, text)) {
        try {
          await savePromptForId(app, oldId, text, { silent: true });
        } catch (err) {
          app._showSettingsSaveStatus('error', err.message || 'Не удалось сохранить пресет');
          app.$.presetSelect.value = oldId;
          return;
        }
      }
    }
    if (newId) localStorage.setItem(LAST_EDIT_KEY, newId);
    app._editingPresetId = newId;
    syncPromptField(app);
    app._hideSettingsSaveStatus();
  }

  function resetPromptSaveBtn(app) {
    const btn = app.$.presetPromptSaveBtn;
    if (!btn) return;
    clearTimeout(app._presetPromptSaveBtnTimer);
    btn.disabled = false;
    btn.setAttribute('aria-label', 'Сохранить промпт на сервер');
    btn.classList.remove('is-success', 'is-error', 'is-saving');
  }

  async function savePrompt(app, options = {}) {
    const presetId = app.$.presetSelect?.value;
    if (!presetId || !app.$.presetSystemPrompt) return false;
    return savePromptForId(app, 
      presetId,
      app.$.presetSystemPrompt.value,
      options,
    );
  }

  async function savePromptIfDirty(app, options = {}) {
    if (!isPromptDirty(app)) return true;
    return savePrompt(app, options);
  }

  async function savePromptForId(app, presetId, text, { silent = false } = {}) {
    const btn = app.$.presetPromptSaveBtn;
    if (!presetId) return false;

    if (btn) {
      btn.disabled = true;
      btn.classList.remove('is-success', 'is-error');
      btn.classList.add('is-saving');
      btn.setAttribute('aria-label', 'Сохранение…');
    }

    try {
      const updated = await app.api(`/api/presets/${presetId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: text }),
      });
      const idx = app.presets.findIndex((p) => p.id === presetId);
      if (idx >= 0) app.presets[idx] = updated;
      markDraftSynced(app, presetId, text);
      if (btn) {
        btn.classList.remove('is-saving');
        btn.classList.add('is-success');
        btn.setAttribute('aria-label', 'Сохранено');
      }
      app.log?.info('settings', `Глобальный промпт пресета ${presetId} сохранён`);
      return true;
    } catch (err) {
      writeDraft(app, presetId, text, false);
      if (btn) {
        btn.classList.remove('is-saving');
        btn.classList.add('is-error');
        btn.setAttribute('aria-label', 'Ошибка сохранения');
      }
      if (!silent) {
        app._showSettingsSaveStatus('error', err.message || 'Не удалось сохранить промпт');
      }
      throw err;
    } finally {
      if (btn) {
        btn.disabled = false;
        clearTimeout(app._presetPromptSaveBtnTimer);
        app._presetPromptSaveBtnTimer = setTimeout(() => {
          btn.classList.remove('is-success', 'is-error', 'is-saving');
          btn.setAttribute('aria-label', 'Сохранить промпт на сервер');
        }, 2200);
      }
    }
  }

  async function setDefault(app) {
    const presetId = app.$.presetSelect?.value;
    const btn = app.$.presetSetDefaultBtn;
    if (!presetId || !btn) return;
    btn.disabled = true;
    try {
      await savePromptIfDirty(app, { silent: true });
      await app.api(`/api/presets/${presetId}/set-default`, { method: 'POST' });
      await load(app);
      app.$.presetSelect.value = presetId;
      app._editingPresetId = presetId;
      syncPromptField(app);
      app.log?.info('settings', `Пресет ${presetId} — по умолчанию`);
    } catch (err) {
      app._showSettingsSaveStatus('error', err.message || 'Не удалось обновить пресет');
    } finally {
      updateDefaultButton(app);
    }
  }

  function bindPresetEvents(app) {
    app.$.presetSelect?.addEventListener('change', () => onSelectChange(app));
    app.$.chatPresetSelect?.addEventListener('change', () => onChatPresetChange(app));
    app.$.presetPromptSaveBtn?.addEventListener('click', () => savePrompt(app));
    app.$.presetSetDefaultBtn?.addEventListener('click', () => setDefault(app));
    app.$.presetSystemPrompt?.addEventListener('input', () => onPromptInput(app));
  }

  window.WebChatPresets = {
    load,
    populateGlobalSelect,
    populateConvSelect,
    updateChatToolbar,
    syncPromptField,
    savePrompt,
    savePromptIfDirty,
    savePromptForId,
    flushDraftsToStorage,
    hasUnsyncedDrafts,
    bindPresetEvents,
    getLastUsedPresetId,
    rememberLastChatPreset,
  };
})();