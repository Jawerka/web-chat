/**
 * Настройки чата: беседа, LLM/SD, интеграции (P5.4).
 * Подключается до chat.js; API: WebChatSettings.*
 */
(function () {
  'use strict';

  function setSettingsChatTitle(app, title) {
    const el = app.$.settingsChatTitle;
    if (!el) return;
    if (!app.currentConvId) {
      el.disabled = true;
      el.value = '';
      el.placeholder = 'Выберите или создайте беседу';
      app._updateExportButton();
      return;
    }
    el.disabled = false;
    el.value = title ?? app.currentConv?.title ?? '';
    el.placeholder = 'Название беседы';
    app._updateExportButton();
  }

  function settingsChatTitleDraft(app) {
    const raw = app.$.settingsChatTitle?.value?.trim() ?? '';
    return raw || 'Новая беседа';
  }

  async function save(app) {
    if (!app.$.settingsSaveBtn) return;
    const convPresetId = app.$.convPresetSelect?.value;

    const btn = app.$.settingsSaveBtn;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.classList.remove('is-success');
    btn.classList.add('is-saving');
    btn.setAttribute('aria-label', 'Сохранение…');
    hideSaveStatus(app);

    try {
      if (app.currentConvId) {
        const patch = {};
        const nextTitle = settingsChatTitleDraft(app);
        if (app.currentConv && app.currentConv.title !== nextTitle) {
          patch.title = nextTitle;
        }
        if (convPresetId && app.currentConv?.preset_id !== convPresetId) {
          patch.preset_id = convPresetId;
        }
        if (Object.keys(patch).length > 0) {
          app.currentConv = await app.api(`/api/conversations/${app.currentConvId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
          });
          setSettingsChatTitle(app, app.currentConv.title);
          const conv = app.conversations.find((c) => c.id === app.currentConvId);
          if (conv) conv.title = app.currentConv.title;
          app.renderConvList();
          if (patch.preset_id) {
            app.populateConvPresetSelect(patch.preset_id);
          }
        }
      }

      await app.savePresetPromptIfDirty({ silent: true });

      WebChatAppearance.applyFontSize(app);
      saveModelOverride(app);
      saveIntegrationUrls(app);
      await loadLlmModelInfo(app);
      await applySdModelSelection(app, { showStatus: false });
      if (app.$.useServerModel) {
        localStorage.setItem(
          'webchat_use_server_model',
          app.$.useServerModel.checked ? 'true' : 'false',
        );
      }

      btn.classList.remove('is-saving');
      btn.classList.add('is-success');
      btn.setAttribute('aria-label', 'Сохранено');
      app.log?.info('settings', 'Настройки сохранены');
    } catch (err) {
      btn.classList.remove('is-saving', 'is-success');
      btn.setAttribute('aria-label', 'Сохранить настройки');
      showSaveStatus(app, 'error', err.message || 'Не удалось сохранить');
    } finally {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      btn.classList.remove('is-saving');
      clearTimeout(app._settingsSaveBtnTimer);
      app._settingsSaveBtnTimer = setTimeout(() => {
        if (!app.$.settingsSaveBtn) return;
        app.$.settingsSaveBtn.classList.remove('is-success');
        app.$.settingsSaveBtn.setAttribute('aria-label', 'Сохранить настройки');
      }, 2200);
    }
  }

  function hideSaveStatus(app) {
    if (!app.$.settingsSaveStatus) return;
    clearTimeout(app._settingsSaveStatusTimer);
    app.$.settingsSaveStatus.textContent = '';
    app.$.settingsSaveStatus.className = 'settings-save-status';
  }

  function showSaveStatus(app, kind, message) {
    if (!app.$.settingsSaveStatus) return;
    clearTimeout(app._settingsSaveStatusTimer);
    app.$.settingsSaveStatus.textContent = message;
    app.$.settingsSaveStatus.className = `settings-save-status is-${kind} is-visible`;
    app._settingsSaveStatusTimer = setTimeout(() => {
      hideSaveStatus(app);
    }, 4000);
  }
  function loadModelSettings(app) {
    if (!app.$.useServerModel) return;
    const stored = localStorage.getItem('webchat_use_server_model');
    if (stored !== null) {
      app.$.useServerModel.checked = stored !== 'false';
    }
    app.$.useServerModel.addEventListener('change', () => {
      localStorage.setItem(
        'webchat_use_server_model',
        app.$.useServerModel.checked ? 'true' : 'false',
      );
      syncModelInputState(app);
    });
  }

  function normalizeServiceUrl(app, raw, { stripV1 = false } = {}) {
    const text = (raw || '').trim();
    if (!text) return '';
    let url = text.replace(/\/+$/, '');
    if (stripV1) {
      url = url.replace(/\/v1$/i, '');
    }
    return url;
  }

  function loadIntegrationUrlFields(app) {
    const llmDefault = app.config?.llm_base_url || '';
    const sdDefault = app.config?.sd_webui_url || '';
    if (app.$.llmBaseUrlInput) {
      app.$.llmBaseUrlInput.value = localStorage.getItem('webchat_llm_base_url')
        || llmDefault;
    }
    if (app.$.sdWebuiUrlInput) {
      app.$.sdWebuiUrlInput.value = localStorage.getItem('webchat_sd_webui_url')
        || sdDefault;
    }
  }

  function saveIntegrationUrls(app) {
    const llm = normalizeServiceUrl(app, app.$.llmBaseUrlInput?.value);
    const sd = normalizeServiceUrl(app, app.$.sdWebuiUrlInput?.value);
    if (llm) {
      localStorage.setItem('webchat_llm_base_url', llm);
    } else {
      localStorage.removeItem('webchat_llm_base_url');
    }
    if (sd) {
      localStorage.setItem('webchat_sd_webui_url', sd);
    } else {
      localStorage.removeItem('webchat_sd_webui_url');
    }
    syncTrustedInternalHosts(app, llm, sd);
  }

  function updateTrustedInternalHint(app) {
    const el = document.getElementById('trusted-internal-hint');
    if (!el) return;
    if (!app.config?.auth_enabled) {
      el.classList.add('hidden');
      return;
    }
    const n = app.config.trusted_internal_ip_count ?? 0;
    const env = (app.config.trusted_internal_env_hosts || []).filter(Boolean);
    const ui = (app.config.trusted_internal_ui_hosts || []).filter(Boolean);
    const parts = [];
    if (env.length) parts.push(`.env: ${env.join(', ')}`);
    if (ui.length) parts.push(`настройки чата: ${ui.join(', ')}`);
    el.textContent = parts.length
      ? `Доверенные IP (${n}): ${parts.join(' · ')}. LLM/SD получают /media без cookie.`
      : `Доверенные IP (${n}): укажите адреса LLM/SD — хосты подставятся автоматически.`;
    el.classList.remove('hidden');
  }

  async function syncTrustedInternalHosts(app, llmUrl, sdUrl) {
    if (!app.config?.auth_enabled) return;
    const llm = llmUrl
      ? (llmUrl.includes('/v1') ? llmUrl : `${llmUrl}/v1`)
      : null;
    try {
      const res = await fetch('/api/config/trusted-internal/sync', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ llm_base_url: llm, sd_webui_url: sdUrl || null }),
      });
      if (!res.ok) return;
      const data = await res.json();
      app.config = {
        ...app.config,
        trusted_internal_env_hosts: data.env_hosts,
        trusted_internal_ui_hosts: data.ui_hosts,
        trusted_internal_ip_count: data.ip_count,
      };
      updateTrustedInternalHint(app);
      app.log?.debug('settings', 'Доверенные IP синхронизированы', { ip_count: data.ip_count });
    } catch (err) {
      app.log?.warn('settings', 'Не удалось синхронизировать доверенные IP', err?.message);
    }
  }

  async function loadLlmModelInfo(app) {
    try {
      const base = normalizeServiceUrl(app, app.$.llmBaseUrlInput?.value);
      const qs = base ? `?llm_base_url=${encodeURIComponent(base)}` : '';
      const info = await app.api(`/api/config/llm-model${qs}`);
      app._serverLlmModel = info.resolved || '';
      app._serverLlmSource = info.source || 'auto';
      syncModelInputState(app);
    } catch {
      if (app.$.llmModelInput) {
        app.$.llmModelInput.placeholder = 'Недоступно';
      }
    }
  }

  async function loadSdModelInfo(app) {
    const select = app.$.sdModelSelect;
    if (!select) return;
    const refreshBtn = app.$.sdModelRefreshBtn;
    if (refreshBtn) refreshBtn.disabled = true;
    select.innerHTML = '<option value="">Загрузка списка моделей…</option>';
    select.disabled = true;
    try {
      const base = normalizeServiceUrl(app, app.$.sdWebuiUrlInput?.value);
      const qs = base ? `?sd_webui_url=${encodeURIComponent(base)}` : '';
      const info = await app.api(`/api/config/sd-models${qs}`);
      app._sdModels = Array.isArray(info?.models) ? info.models : [];
      app._sdSelectedServer = String(info?.selected || '');
      syncSdModelSelectState(app);
    } catch (err) {
      app._sdModels = [];
      app._sdSelectedServer = '';
      select.innerHTML = '<option value="">Не удалось загрузить модели</option>';
      select.disabled = true;
      app.log?.warn('settings', 'Не удалось получить список SD моделей', err?.message || err);
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }

  function syncSdModelSelectState(app) {
    const select = app.$.sdModelSelect;
    if (!select) return;
    const models = app._sdModels || [];
    if (!models.length) {
      select.innerHTML = '<option value="">Список моделей пуст</option>';
      select.disabled = true;
      return;
    }
    const serverSelected = (app._sdSelectedServer || '').trim();
    const saved = (localStorage.getItem('webchat_sd_model_checkpoint') || '').trim();
    const candidate = saved || serverSelected;

    select.innerHTML = '';
    for (const item of models) {
      const title = String(item?.title || '').trim();
      if (!title) continue;
      const opt = document.createElement('option');
      opt.value = title;
      opt.textContent = title;
      select.appendChild(opt);
    }
    if (!select.options.length) {
      select.innerHTML = '<option value="">Список моделей пуст</option>';
      select.disabled = true;
      return;
    }
    select.disabled = false;
    if (candidate && [...select.options].some((o) => o.value === candidate)) {
      select.value = candidate;
    } else if (serverSelected && [...select.options].some((o) => o.value === serverSelected)) {
      select.value = serverSelected;
    } else {
      select.selectedIndex = 0;
    }
  }

  async function applySdModelSelection(app, { showStatus = true } = {}) {
    const select = app.$.sdModelSelect;
    if (!select || select.disabled) return;
    const title = (select.value || '').trim();
    if (!title) {
      localStorage.removeItem('webchat_sd_model_checkpoint');
      return;
    }
    if (title === (app._sdSelectedServer || '').trim()) {
      localStorage.setItem('webchat_sd_model_checkpoint', title);
      return;
    }
    const prev = (app._sdSelectedServer || '').trim();
    const sdUrl = normalizeServiceUrl(app, app.$.sdWebuiUrlInput?.value);
    select.disabled = true;
    if (showStatus) showSaveStatus(app, 'info', 'Применяем SD модель…');
    try {
      const res = await fetch('/api/config/sd-models/select', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          title,
          sd_webui_url: sdUrl || null,
          warmup: true,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Не удалось применить SD модель');
      }
      localStorage.setItem('webchat_sd_model_checkpoint', title);
      app._sdSelectedServer = title;
      app.log?.info('settings', `SD модель применена: ${title}`);
      if (showStatus) showSaveStatus(app, 'success', `SD модель активна: ${title}`);
    } catch (err) {
      if (prev && [...select.options].some((o) => o.value === prev)) {
        select.value = prev;
      }
      if (showStatus) {
        showSaveStatus(app, 'error', err?.message || 'Не удалось применить SD модель');
      }
      throw err;
    } finally {
      select.disabled = false;
    }
  }

  function syncModelInputState(app) {
    if (!app.$.llmModelInput || !app.$.useServerModel) return;
    const useServer = app.$.useServerModel.checked;
    app.$.llmModelInput.readOnly = useServer;
    if (useServer) {
      app.$.llmModelInput.value = app._serverLlmModel;
      app.$.llmModelInput.title = app._serverLlmSource === 'config'
        ? 'Из конфигурации сервера'
        : 'Автовыбор с указанного API';
    } else {
      const saved = localStorage.getItem('webchat_llm_model_override') || '';
      app.$.llmModelInput.value = saved;
      app.$.llmModelInput.title = 'Переопределение для запросов из браузера';
    }
  }

  function saveModelOverride(app) {
    if (!app.$.llmModelInput || app.$.useServerModel?.checked) return;
    localStorage.setItem('webchat_llm_model_override', app.$.llmModelInput.value.trim());
  }

  function getActiveLlmModel(app) {
    if (!app.$.useServerModel || app.$.useServerModel.checked) return undefined;
    const v = (app.$.llmModelInput?.value || '').trim();
    return v || undefined;
  }
  window.WebChatSettings = {
    setSettingsChatTitle,
    settingsChatTitleDraft,
    save,
    hideSaveStatus,
    showSaveStatus,
    loadModelSettings,
    normalizeServiceUrl,
    loadIntegrationUrlFields,
    saveIntegrationUrls,
    updateTrustedInternalHint,
    syncTrustedInternalHosts,
    loadLlmModelInfo,
    loadSdModelInfo,
    syncSdModelSelectState,
    applySdModelSelection,
    syncModelInputState,
    saveModelOverride,
    getActiveLlmModel,
  };
})();
