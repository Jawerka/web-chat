/**
 * Предзагрузка моделей LLM и SD (ручная и автоматическая перед отправкой).
 */
(function () {
  'use strict';

  const HEALTH_POLL_MS = 2000;
  const LLM_WAIT_MS = 130000;
  const SOCKET_READY_MS = 25000;
  /** Не прогревать повторно, если недавно уже гоняли модели (мс). */
  const WARM_SKIP_MS = 40 * 60 * 1000;
  const WARMED_AT_KEY = 'webchat_models_warmed_at';
  const SD_PRESET_SLUGS = new Set(['img2img', 'image_gen']);

  function parseErrorDetail(payload, fallback) {
    const detail = payload?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail.map((item) => item?.msg || item).filter(Boolean).join('; ');
    }
    return fallback;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function recordWarmedAt() {
    try {
      localStorage.setItem(WARMED_AT_KEY, String(Date.now()));
    } catch {
      /* ignore */
    }
  }

  function isRecentlyWarmed() {
    try {
      const raw = localStorage.getItem(WARMED_AT_KEY);
      const at = Number(raw);
      return Number.isFinite(at) && at > 0 && (Date.now() - at) < WARM_SKIP_MS;
    } catch {
      return false;
    }
  }

  function setBtnState(btn, { busy = false, label = null, title = null } = {}) {
    if (!btn) return;
    btn.disabled = busy;
    btn.setAttribute('aria-busy', busy ? 'true' : 'false');
    const labelEl = btn.querySelector('.composer-tools-menu-label');
    if (label && labelEl) labelEl.textContent = label;
    if (title) btn.title = title;
  }

  function restoreBtn(btn, defaults) {
    if (!btn) return;
    btn.disabled = false;
    btn.removeAttribute('aria-busy');
    const labelEl = btn.querySelector('.composer-tools-menu-label');
    if (labelEl) labelEl.textContent = defaults.label;
    btn.title = defaults.title;
  }

  function showStatus(app, message, { error = false, success = false } = {}) {
    if (error) {
      app.showError?.(message, 6000);
      return;
    }
    if (success) {
      app.showSuccess?.(message, 4000);
      return;
    }
    app.showUploadSuccess?.(message);
  }

  function integrationUrls(app) {
    const llm = WebChatSettings.normalizeServiceUrl(app, app.$.llmBaseUrlInput?.value);
    const sd = WebChatSettings.normalizeServiceUrl(app, app.$.sdWebuiUrlInput?.value);
    return { llm, sd };
  }

  async function fetchHealth(app) {
    return app.api('/api/health');
  }

  function conversationUsesSd(app) {
    const presets = app.presets || [];
    const pid = app.currentConv?.preset_id
      || (typeof WebChatPresets !== 'undefined' && WebChatPresets.getLastUsedPresetId?.(app));
    const preset = presets.find((p) => String(p.id) === String(pid));
    return SD_PRESET_SLUGS.has(preset?.slug || '');
  }

  async function fetchSdReady(app, sdUrl, { probe = false } = {}) {
    const qs = new URLSearchParams();
    if (sdUrl) qs.set('sd_webui_url', sdUrl);
    if (probe) qs.set('probe', 'true');
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return app.api(`/api/config/sd-ready${suffix}`);
  }

  /**
   * Пропуск только LLM-прогрева (40 мин). SD для image_gen/img2img всегда проверяется отдельно.
   */
  function canSkipLlmWarmup(health) {
    return isRecentlyWarmed() && health?.llm === 'ok';
  }

  /**
   * Нужна ли предзагрузка.
   * LLM в статусе loading — только ожидание (без POST llm-warmup).
   * SD: probe sd-ready для пресетов с генерацией — даже после недавнего прогрева.
   */
  async function resolveNeeds(app, health, { force = false } = {}) {
    const skipLlm = !force && canSkipLlmWarmup(health);

    let llmWarmup = false;
    let llmWait = false;
    if (!skipLlm) {
      const llmSvc = health?.services?.find((s) => s.id === 'llm');
      llmWait = llmSvc?.status === 'loading';
      llmWarmup = health?.llm !== 'ok' && !llmWait;
    }

    let needSd = false;
    if (conversationUsesSd(app)) {
      needSd = health?.sd !== 'ok';
      if (!needSd) {
        const { sd: sdUrl } = integrationUrls(app);
        try {
          const ready = await fetchSdReady(app, sdUrl);
          if (!ready?.ready) needSd = true;
        } catch {
          needSd = true;
        }
      }
    }

    const skippedRecent = skipLlm && !llmWarmup && !llmWait && !needSd;
    return {
      llmWarmup,
      llmWait,
      sd: needSd,
      any: llmWarmup || llmWait || needSd,
      skippedRecent,
    };
  }

  async function resolveSdCheckpoint(app, sdUrl) {
    const saved = (localStorage.getItem('webchat_sd_model_checkpoint') || '').trim();
    const qs = sdUrl ? `?sd_webui_url=${encodeURIComponent(sdUrl)}` : '';
    const info = await app.api(`/api/config/sd-models${qs}`);
    const models = Array.isArray(info?.models) ? info.models : [];
    const titles = new Set(models.map((m) => String(m?.title || '').trim()).filter(Boolean));
    const serverSelected = String(info?.selected || '').trim();
    const candidate = saved || serverSelected;
    if (candidate && titles.has(candidate)) return candidate;
    if (serverSelected && titles.has(serverSelected)) return serverSelected;
    return models[0]?.title ? String(models[0].title).trim() : '';
  }

  function beginPreloadSession(app) {
    app._preloadingModels = true;
    app.socket?.setHoldReconnect?.(true);
    WebChatComposer.syncSendState(app);
  }

  async function endPreloadSession(app) {
    app.socket?.setHoldReconnect?.(false);
    app._preloadingModels = false;
    WebChatComposer.syncSendState(app);
    if (typeof app._ensureSocketReady !== 'function') return true;
    const ok = await app._ensureSocketReady(SOCKET_READY_MS);
    if (!ok) {
      app.log?.warn('preload', 'WebSocket не восстановлен после прогрева');
    }
    return ok;
  }

  async function waitForLlmReady(app, { onProgress, maxWaitMs = LLM_WAIT_MS } = {}) {
    const started = Date.now();
    onProgress?.('LLM: загрузка модели…');
    while (Date.now() - started < maxWaitMs) {
      const health = await fetchHealth(app);
      if (health?.llm === 'ok') return true;
      const llm = health?.services?.find((s) => s.id === 'llm');
      if (llm?.status === 'ok') return true;
      if (llm?.status === 'loading') {
        onProgress?.(`LLM: ${llm.detail || 'загрузка модели…'}`);
      } else if (health?.llm !== 'ok') {
        onProgress?.('LLM: ожидание ответа…');
      }
      await sleep(HEALTH_POLL_MS);
    }
    throw new Error('Таймаут ожидания загрузки LLM. Повторите отправку позже.');
  }

  function startLlmLoadingPoll(app, onLlmStatus) {
    if (!onLlmStatus) return () => {};
    let stopped = false;
    let lastText = '';
    const tick = async () => {
      if (stopped) return;
      try {
        const health = await fetchHealth(app);
        const llm = health?.services?.find((s) => s.id === 'llm');
        let text = '';
        if (llm?.status === 'loading') {
          text = `LLM: ${llm.detail || 'загрузка модели…'}`;
        } else if (health?.llm !== 'ok') {
          text = 'LLM: ожидание ответа…';
        }
        if (text && text !== lastText) {
          lastText = text;
          onLlmStatus(text);
        }
      } catch {
        /* ignore */
      }
    };
    void tick();
    const timer = setInterval(() => { void tick(); }, HEALTH_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }

  async function warmupLlm(app, { llmUrl, model, onProgress }) {
    onProgress?.('Загрузка LLM…');
    const body = {};
    if (llmUrl) body.llm_base_url = llmUrl;
    if (model) body.model = model;

    const stopPoll = startLlmLoadingPoll(app, onProgress);
    try {
      const res = await fetch('/api/config/llm-warmup', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        const detail = parseErrorDetail(payload, 'Не удалось прогреть LLM');
        if (res.status === 504 || /таймаут|loading|503/i.test(detail)) {
          throw new Error(`${detail}. Дождитесь загрузки модели на сервере LLM и повторите.`);
        }
        throw new Error(detail);
      }
      const data = await res.json();
      return String(data.model || model || '').trim();
    } finally {
      stopPoll();
    }
  }

  async function warmupSd(app, { sdUrl, title, onProgress }) {
    onProgress?.(title ? `Загрузка SD: ${title}…` : 'Загрузка SD…');
    const body = { sd_webui_url: sdUrl || null };
    if (title) body.title = title;
    const res = await fetch('/api/config/sd-warmup', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new Error(parseErrorDetail(payload, 'Не удалось прогреть SD'));
    }
    const data = await res.json();
    const selected = String(data.selected || title || '').trim();
    if (selected) {
      localStorage.setItem('webchat_sd_model_checkpoint', selected);
      app._sdSelectedServer = selected;
    }
    return selected;
  }

  async function executePreload(app, needs, { onProgress } = {}) {
    const { llm: llmUrl, sd: sdUrl } = integrationUrls(app);
    const llmModel = WebChatSettings.getActiveLlmModel(app);
    const result = { llm: '', sd: '' };

    const llmTask = async () => {
      if (needs.llmWait) {
        await waitForLlmReady(app, { onProgress });
        result.llm = llmModel || 'ready';
      } else if (needs.llmWarmup) {
        result.llm = await warmupLlm(app, {
          llmUrl,
          model: llmModel,
          onProgress,
        });
      }
    };

    const sdTask = async () => {
      if (!needs.sd) return;
      const sdTitle = await resolveSdCheckpoint(app, sdUrl);
      result.sd = await warmupSd(app, {
        sdUrl,
        title: sdTitle,
        onProgress,
      });
    };

    await Promise.all([llmTask(), sdTask()]);
    return result;
  }

  function formatReadyMessage(result, needs) {
    const parts = [];
    if ((needs.llmWarmup || needs.llmWait) && result.llm) parts.push(`LLM ${result.llm}`);
    if (needs.sd && result.sd) parts.push(`SD ${result.sd}`);
    if (!parts.length) return 'Модели готовы';
    return `Модели готовы: ${parts.join(', ')}`;
  }

  async function ensureBeforeSend(app, { quiet = true } = {}) {
    if (app._preloadingModels) return false;

    let health;
    try {
      health = await fetchHealth(app);
    } catch (err) {
      app.log?.warn('preload', `health недоступен: ${err?.message || err}`);
      return true;
    }

    const needs = await resolveNeeds(app, health);
    if (!needs.any) {
      if (needs.skippedRecent) {
        app.log?.debug('preload', 'Прогрев пропущен: модели недавно использовались');
      }
      return true;
    }

    beginPreloadSession(app);

    try {
      const result = await executePreload(app, needs, {
        onProgress: quiet ? undefined : (text) => showStatus(app, text),
      });
      const message = formatReadyMessage(result, needs);
      app.log?.info('preload', `Автозагрузка перед отправкой: ${message}`);
      if (!quiet) showStatus(app, message, { success: true });
      const socketOk = await endPreloadSession(app);
      if (!socketOk) {
        showStatus(app, 'Соединение с сервером потеряно во время загрузки моделей. Подождите переподключения и повторите.', { error: true });
        return false;
      }
      recordWarmedAt();
      return true;
    } catch (err) {
      const message = err?.message || 'Не удалось загрузить модели';
      showStatus(app, message, { error: true });
      app.log?.warn('preload', message);
      await endPreloadSession(app);
      return false;
    }
  }

  async function run(app) {
    const btn = app.$.preloadModelsBtn;
    if (!btn || app._preloadingModels) return;

    const defaults = {
      label: 'Загрузить модели',
      title: 'Предзагрузка моделей LLM и SD',
    };

    let needs = { llmWarmup: true, llmWait: false, sd: true, any: true };
    try {
      const health = await fetchHealth(app);
      needs = await resolveNeeds(app, health, { force: true });
    } catch {
      /* принудительный прогрев по health ниже */
    }

    if (!needs.any) {
      showStatus(app, 'Модели уже готовы', { success: true });
      return;
    }

    beginPreloadSession(app);
    WebChatComposer.closeToolsMenu(app);
    setBtnState(btn, { busy: true, label: 'Загрузка…', title: 'Предзагрузка моделей…' });

    try {
      const result = await executePreload(app, needs, {
        onProgress: (text) => setBtnState(btn, { busy: true, label: text, title: text }),
      });
      const message = formatReadyMessage(result, needs);
      const socketOk = await endPreloadSession(app);
      if (socketOk) {
        recordWarmedAt();
        showStatus(app, message, { success: true });
        app.log?.info('preload', message);
      } else {
        showStatus(
          app,
          `${message}. Соединение с чатом не восстановлено — дождитесь переподключения перед отправкой.`,
          { error: true },
        );
        app.log?.warn('preload', `${message}; WebSocket не восстановлен после прогрева`);
      }
    } catch (err) {
      const message = err?.message || 'Не удалось загрузить модели';
      showStatus(app, message, { error: true });
      app.log?.warn('preload', message);
      await endPreloadSession(app);
    } finally {
      WebChatComposer.syncSendState(app);
      restoreBtn(btn, defaults);
    }
  }

  window.WebChatPreloadModels = {
    run,
    ensureBeforeSend,
    resolveNeeds,
    conversationUsesSd,
    canSkipLlmWarmup,
    recordWarmedAt,
    isRecentlyWarmed,
  };
})();
