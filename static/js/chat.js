/**
 * web-chat UI — REST + WebSocket
 */
/* global formatMarkdown */

/** Зона у низа чата: внутри — «прилипание» к автоскроллу. */
const SCROLL_STICKY_PX = 100;

const PRESET_DRAFTS_STORAGE_KEY = 'webchat_preset_drafts_v1';
/** Вложения после перехода из галереи (скрепка → новый чат) */
const PENDING_ATTACHMENTS_KEY = 'webchat_pending_attachments';
const PRESET_LAST_EDIT_STORAGE_KEY = 'webchat_preset_last_edit_id';

/** Короткие подписи в плавающем селекте пресета чата */
const CHAT_PRESET_SHORT_LABELS = {
  default: 'Default',
  image_gen: 'txt2img',
  img2img: 'img2img',
  document_analysis: 'Docs',
};

const TOOL_PROGRESS_LABELS = {
  generate_image: 'Генерация изображения',
  img2img: 'Доработка изображения',
  upscale_images: 'Увеличение разрешения',
  get_gallery: 'Загрузка галереи',
  extract_text: 'Извлечение текста из документа',
};

const MESSAGE_STATUS_HTML = `
  <div class="message-status" role="status" aria-live="polite">
    <div class="message-status-pill">
      <span class="message-status-dots" aria-hidden="true">
        <span></span><span></span><span></span>
      </span>
      <span class="message-status-text"></span>
    </div>
  </div>`;

function resolveMediaUrl(url) {
  if (!url) return url;
  const s = String(url).trim();
  if (s.startsWith('/media/')) {
    return `${window.location.origin}${s}`;
  }
  try {
    const u = new URL(s, window.location.origin);
    if (u.pathname.startsWith('/media/')) {
      return `${window.location.origin}${u.pathname}${u.search}`;
    }
  } catch {
    /* ignore */
  }
  return s;
}

function extractMarkdownImageUrls(text) {
  if (!text) return [];
  const urls = [];
  const re = /!\[[^\]]*\]\(([^)]+)\)/g;
  let match = re.exec(text);
  while (match) {
    urls.push(match[1]);
    match = re.exec(text);
  }
  return urls;
}

/** Убрать markdown-картинки из текста ассистента (картинки — в content_json / .message-images). */
function stripMarkdownImages(text) {
  if (!text) return '';
  return String(text)
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function imageUrlsFromParts(parts) {
  if (!parts || !parts.length) return [];
  const urls = [];
  for (const p of parts) {
    if (p.type === 'image_url' && p.image_url && p.image_url.url) {
      urls.push(p.image_url.url);
    } else if (p.asset_id) {
      urls.push(`/media/asset/${p.asset_id}`);
    }
  }
  return urls;
}

function imageUrlsFromMessage(m) {
  if (!m) return [];
  const cj = m.content_json || {};
  const fromJson = cj.images || [];
  const fromAssets = (cj.image_asset_ids || []).map((id) => `/media/asset/${id}`);
  const fromParts = m.role === 'user' ? imageUrlsFromParts(cj.parts) : [];
  const hasStructured = fromJson.length > 0 || fromAssets.length > 0;
  const fromMd = (
    m.role === 'assistant' && !hasStructured
  ) ? extractMarkdownImageUrls(m.content_text) : [];
  const merged = [...fromAssets, ...fromJson, ...fromParts, ...fromMd];
  return [...new Set(merged.map(resolveMediaUrl).filter(Boolean))];
}

/** Ключ для сравнения URL картинок (pathname, без origin). */
function imageUrlKey(url) {
  const resolved = resolveMediaUrl(url);
  if (!resolved) return '';
  try {
    return new URL(resolved, window.location.origin).pathname;
  } catch {
    return resolved;
  }
}

const MSG_ICONS = {
  copy: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  edit: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  regen: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  delete: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
};

class ChatSocket {
  constructor(conversationId, handlers) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = `${proto}//${window.location.host}/ws/${conversationId}`;
    this.conversationId = conversationId;
    this.handlers = handlers;
    this.ws = null;
    this._pingTimer = null;
    this._reconnectTimer = null;
    this._reconnectAttempt = 0;
    this._shouldReconnect = true;
  }

  connect() {
    if (this.ws) this.disconnect(false);
    this._shouldReconnect = true;
    this.handlers.onConnecting?.();

    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this._reconnectAttempt = 0;
      this._pingTimer = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
      this.handlers.onOpen?.();
    };
    this.ws.onmessage = (e) => {
      try {
        this._dispatch(JSON.parse(e.data));
      } catch (err) {
        window.appLog?.error('ws', 'Ошибка разбора WS-сообщения', err.message);
      }
    };
    this.ws.onclose = () => {
      clearInterval(this._pingTimer);
      this.handlers.onClose?.();
      this._scheduleReconnect();
    };
    this.ws.onerror = () => this.handlers.onError?.();
  }

  disconnect(stopReconnect = true) {
    if (stopReconnect) this._shouldReconnect = false;
    clearInterval(this._pingTimer);
    clearTimeout(this._reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  _scheduleReconnect() {
    if (!this._shouldReconnect) return;
    const delay = Math.min(1000 * 2 ** this._reconnectAttempt, 15000);
    this._reconnectAttempt += 1;
    this.handlers.onReconnecting?.(delay);
    this._reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  sendUserMessage(text, attachmentIds, integration) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Нет соединения с сервером');
    }
    const payload = {
      type: 'user_message',
      text,
      attachment_ids: attachmentIds,
      ...integration,
    };
    this.ws.send(JSON.stringify(payload));
  }

  cancel() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cancel' }));
    }
  }

  sendRegenerate(messageId, integration = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Нет соединения с сервером');
    }
    this.ws.send(JSON.stringify({
      type: 'regenerate',
      message_id: messageId,
      ...integration,
    }));
  }

  _dispatch(msg) {
    const h = this.handlers;
    switch (msg.type) {
      case 'connected': h.onConnected?.(msg); break;
      case 'assistant_draft': h.onAssistantDraft?.(msg); break;
      case 'ack': h.onAck?.(msg); break;
      case 'text_delta': h.onTextDelta?.(msg.content || ''); break;
      case 'image': h.onImages?.(msg.urls || []); break;
      case 'tool_start': h.onToolStart?.(msg.name, msg.arguments); break;
      case 'tool_done': h.onToolDone?.(msg.name, msg.summary); break;
      case 'done': h.onDone?.(msg); break;
      case 'error': h.onWsError?.(msg.message, msg.code); break;
      default: break;
    }
  }
}

class ChatApp {
  constructor() {
    this.conversations = [];
    this.presets = [];
    this.currentConvId = null;
    this.currentConv = null;
    this.pendingAttachments = [];
    this.socket = null;
    this.streaming = false;
    this.streamText = '';
    this.streamEl = null;
    this.streamImagesEl = null;
    this.config = { max_files_per_message: 10 };
    this._errorTimer = null;
    this.editingMessageId = null;
    this.editingRole = null;
    this._regenerating = false;
    this._inputPlaceholderDefault = 'Сообщение';
    this._serverLogLines = [];
    this._logsUnsub = null;
    this._pendingDeleteConvId = null;
    this._pendingDeleteBtn = null;
    this._scrollStuckToBottom = true;
    this._lightboxUrls = [];
    this._lightboxIndex = 0;
    this._lightboxTouchStart = null;
    this._generationSyncTimer = null;
    this._generationResumeActive = false;
    this._generationWatchRunning = false;
    this._serverLlmModel = '';
    this._serverLlmSource = 'auto';
    this._settingsSaveStatusTimer = null;
    this._settingsSaveBtnTimer = null;
    this._presetPromptSaveBtnTimer = null;
    this._presetDraftDebounceTimer = null;
    this._editingPresetId = null;
    this._searchDebounceTimer = null;
    this._inlineTitleConvId = null;
    this.log = window.appLog;
    this.promptMacros = new PromptMacrosUI(this);

    this.$ = {
      backdrop: document.getElementById('sidebar-backdrop'),
      convSearch: document.getElementById('conv-search'),
      convSearchToggle: document.getElementById('conv-search-toggle'),
      convSearchStack: document.getElementById('conv-search-stack'),
      convSearchPanel: document.getElementById('conv-search-panel'),
      convSearchClose: document.getElementById('conv-search-close'),
      convSearchResults: document.getElementById('conv-search-results'),
      convList: document.getElementById('conv-list'),
      convEmpty: document.getElementById('conv-empty'),
      convSidebar: document.getElementById('conv-sidebar'),
      settingsPanel: document.getElementById('settings-panel'),
      logsPanel: document.getElementById('logs-panel'),
      macroInsertBtn: document.getElementById('macro-insert-btn'),
      settingsChatTitle: document.getElementById('settings-chat-title'),
      exportConversationBtn: document.getElementById('export-conversation-btn'),
      convPresetSelect: document.getElementById('conv-preset-select'),
      chatPresetToolbar: document.getElementById('chat-preset-toolbar'),
      chatPresetSelect: document.getElementById('chat-preset-select'),
      presetSelect: document.getElementById('preset-select'),
      presetSystemPrompt: document.getElementById('preset-system-prompt'),
      presetPromptSaveBtn: document.getElementById('preset-prompt-save-btn'),
      presetSetDefaultBtn: document.getElementById('preset-set-default-btn'),
      settingsSaveBtn: document.getElementById('settings-save-btn'),
      settingsSaveStatus: document.getElementById('settings-save-status'),
      settingsBtn: document.getElementById('settings-btn'),
      logsBtn: document.getElementById('logs-btn'),
      connStatus: document.getElementById('conn-status'),
      connStatusLabel: document.getElementById('conn-status-label'),
      placeholder: document.getElementById('placeholder'),
      chatHistory: document.getElementById('chat-history'),
      chatMessages: document.getElementById('chat-messages'),
      chatComposer: document.getElementById('chat-composer'),
      userInput: document.getElementById('user-input'),
      sendBtn: document.getElementById('send-btn'),
      cancelBtn: document.getElementById('cancel-btn'),
      fileInput: document.getElementById('file-input'),
      attachmentStrip: document.getElementById('attachment-strip'),
      errorBanner: document.getElementById('error-banner'),
      errorBannerText: document.getElementById('error-banner-text'),
      scrollBtn: document.getElementById('scroll-to-bottom-btn'),
      loadingOverlay: document.getElementById('loading-overlay'),
      newConvModal: document.getElementById('new-conv-modal'),
      newConvPreset: document.getElementById('new-conv-preset'),
      lightbox: document.getElementById('lightbox'),
      lightboxImg: document.getElementById('lightbox-img'),
      lightboxPrev: document.getElementById('lightbox-prev'),
      lightboxNext: document.getElementById('lightbox-next'),
      lightboxCounter: document.getElementById('lightbox-counter'),
      lightboxSave: document.getElementById('lightbox-save'),
      lightboxAttachCurrent: document.getElementById('lightbox-attach-current'),
      themeToggle: document.getElementById('theme-toggle'),
      themeToggleLabel: document.getElementById('theme-toggle-label'),
      llmBaseUrlInput: document.getElementById('llm-base-url-input'),
      llmModelInput: document.getElementById('llm-model-input'),
      sdWebuiUrlInput: document.getElementById('sd-webui-url-input'),
      useServerModel: document.getElementById('use-server-model'),
      fontSizeInput: document.getElementById('font-size'),
      fontSizeDecrease: document.getElementById('font-size-decrease'),
      fontSizeIncrease: document.getElementById('font-size-increase'),
      logsOutput: document.getElementById('logs-output'),
      logsCount: document.getElementById('logs-count'),
    };

    this.log?.info('app', 'Интерфейс загружен');
    this._bindEvents();
    this._loadTheme();
    this._updateThemeToggleLabel();
    this._loadFontSize();
    this._loadModelSettings();
    this.showPanel('main');
    this.init();
  }

  async init() {
    try {
      const cfg = await this.api('/api/config');
      this.config = { ...this.config, ...cfg };
      this._loadIntegrationUrlFields();
    } catch { /* optional */ }
    this.loadLlmModelInfo().catch(() => {});

    await Promise.all([this.loadPresets(), this.loadConversations(), this.promptMacros.load()]);
    this.promptMacros.bindInputAutocomplete(this.$.userInput);
    const saved = localStorage.getItem('webchat_conv_id');
    if (saved && this.conversations.some((c) => c.id === saved)) {
      await this.selectConversation(saved);
    }
  }

  _bindEvents() {
    document.getElementById('btn-new-chat').addEventListener('click', () => this.openNewConvModal());
    document.getElementById('placeholder-new-chat').addEventListener('click', () => this.openNewConvModal());
    const closeNewConvModal = () => this.$.newConvModal.close();
    document.getElementById('new-conv-cancel')?.addEventListener('click', closeNewConvModal);
    document.getElementById('new-conv-close')?.addEventListener('click', closeNewConvModal);
    document.getElementById('new-conv-form').addEventListener('submit', (e) => {
      e.preventDefault();
      this.createConversation();
    });

    document.getElementById('menu-btn').addEventListener('click', () => this.openSidebar());
    this.$.backdrop.addEventListener('click', () => this.closeSidebar());

    this.$.settingsBtn?.addEventListener('click', () => this.showPanel('settings'));
    this.$.logsBtn?.addEventListener('click', () => this.openLogsPanel());
    this.$.macroInsertBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.promptMacros.isPickerOpen()) {
        this.promptMacros.closePicker();
      } else {
        this.promptMacros.openPicker();
      }
    });
    document.addEventListener('click', (e) => {
      const pop = document.getElementById('macro-picker-popover');
      if (!pop || pop.classList.contains('hidden')) return;
      if (pop.contains(e.target) || this.$.macroInsertBtn?.contains(e.target)) return;
      this.promptMacros.closePicker();
    });
    document.getElementById('settings-close')?.addEventListener('click', () => this.showPanel('main'));
    document.getElementById('logs-close')?.addEventListener('click', () => this.closeLogsPanel());
    this.$.themeToggle?.addEventListener('click', () => this.toggleTheme());
    this.$.llmModelInput?.addEventListener('change', () => this._saveModelOverride());
    this.$.fontSizeDecrease?.addEventListener('click', () => this.changeFontSize(-1));
    this.$.fontSizeIncrease?.addEventListener('click', () => this.changeFontSize(1));
    this.$.fontSizeInput?.addEventListener('change', () => this.applyFontSize());
    document.getElementById('logs-copy-all')?.addEventListener('click', () => this.copyAllLogs());
    document.getElementById('logs-clear-all')?.addEventListener('click', () => this.clearAllLogs());
    document.getElementById('error-banner-close').addEventListener('click', () => this.hideError());
    this.$.sendBtn.addEventListener('click', () => this.sendMessage());
    this.$.cancelBtn.addEventListener('click', () => this.cancelGeneration());

    this.$.userInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        if (this.promptMacros.isAutocompleteOpen()) {
          e.preventDefault();
          this.promptMacros.applyAutocompleteSelection();
          return;
        }
        e.preventDefault();
        this.sendMessage();
      }
    });
    this.$.userInput.addEventListener('input', () => this.autoResizeInput());
    requestAnimationFrame(() => this.autoResizeInput());
    this.$.fileInput.addEventListener('change', (e) => this.uploadFiles(e.target.files));
    this.$.presetSelect?.addEventListener('change', () => this.onPresetSelectChange());
    this.$.chatPresetSelect?.addEventListener('change', () => this.onChatPresetChange());
    this.$.presetPromptSaveBtn?.addEventListener('click', () => this.savePresetPrompt());
    this.$.presetSetDefaultBtn?.addEventListener('click', () => this.setDefaultPreset());
    this.$.presetSystemPrompt?.addEventListener('input', () => this._onPresetPromptInput());
    window.addEventListener('beforeunload', (e) => {
      if (this._hasUnsyncedPresetDrafts()) {
        this._flushPresetDraftsToStorage();
        e.preventDefault();
        e.returnValue = '';
      }
    });
    this.$.settingsSaveBtn?.addEventListener('click', () => this.saveSettings());
    this.$.exportConversationBtn?.addEventListener('click', () => this.exportCurrentConversation());
    this.$.convSearchToggle?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._toggleConvSearchPanel();
    });
    this.$.convSearchClose?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._closeConvSearchPanel();
    });
    this.$.convSearch?.addEventListener('input', () => this._onConvSearchInput());
    this.$.convSearch?.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        this._closeConvSearchPanel();
      }
    });
    this._convSearchOutsideClick = (e) => {
      if (!this._isConvSearchOpen()) return;
      const t = e.target;
      if (
        this.$.convSearchStack?.contains(t)
        || this.$.convSearchToggle?.contains(t)
      ) {
        return;
      }
      this._closeConvSearchPanel();
    };
    document.addEventListener('mousedown', this._convSearchOutsideClick);
    this.$.settingsChatTitle?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        this.saveSettings();
      }
    });
    this.$.chatHistory.addEventListener('scroll', () => this._onChatScroll());
    this.$.scrollBtn.addEventListener('click', () => this.scrollToBottom(true));

    let convTooltipResizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(convTooltipResizeTimer);
      convTooltipResizeTimer = setTimeout(() => this._updateConvTitleTooltips(), 150);
    });

    document.getElementById('lightbox-close').addEventListener('click', () => this.closeLightbox());
    this.$.lightboxSave?.addEventListener('click', (e) => {
      e.stopPropagation();
      void this.downloadLightboxImage();
    });
    this.$.lightboxAttachCurrent?.addEventListener('click', (e) => {
      e.stopPropagation();
      void this.attachLightboxImageToComposer();
    });
    this.$.lightboxPrev.addEventListener('click', (e) => {
      e.stopPropagation();
      this._lightboxStep(-1);
    });
    this.$.lightboxNext.addEventListener('click', (e) => {
      e.stopPropagation();
      this._lightboxStep(1);
    });
    this.$.lightbox.addEventListener('click', (e) => {
      if (e.target === this.$.lightbox) this.closeLightbox();
    });
    this.$.lightbox.addEventListener('touchstart', (e) => this._onLightboxTouchStart(e), { passive: true });
    this.$.lightbox.addEventListener('touchend', (e) => this._onLightboxTouchEnd(e), { passive: true });

    document.addEventListener('keydown', (e) => {
      if (!this.$.lightbox.classList.contains('hidden')) {
        if (e.key === 'ArrowLeft') {
          e.preventDefault();
          this._lightboxStep(-1);
          return;
        }
        if (e.key === 'ArrowRight') {
          e.preventDefault();
          this._lightboxStep(1);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          this.closeLightbox();
          return;
        }
      }
      if (e.key === 'Escape') {
        if (this._isLogsPanelOpen()
          || !this.$.settingsPanel?.classList.contains('hidden')) {
          this.showPanel('main');
          return;
        }
        if (this.editingMessageId) {
          this.editingMessageId = null;
          this.editingRole = null;
          this.$.userInput.value = '';
          this.$.userInput.placeholder = this._inputPlaceholderDefault;
          this.autoResizeInput();
          return;
        }
        this.closeLightbox();
        this.closeSidebar();
        if (this.$.newConvModal.open) this.$.newConvModal.close();
      }
    });

    const drop = document.getElementById('drop-zone');
    drop.addEventListener('dragover', (e) => {
      e.preventDefault();
      drop.classList.add('drag-over');
    });
    drop.addEventListener('dragleave', (e) => {
      if (!drop.contains(e.relatedTarget)) drop.classList.remove('drag-over');
    });
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('drag-over');
      if (e.dataTransfer?.files?.length) this.uploadFiles(e.dataTransfer.files);
    });

    this._onDocumentClickCancelDelete = (e) => {
      if (!this._pendingDeleteConvId) return;
      if (e.target.closest(`.conv-item-delete[data-id="${this._pendingDeleteConvId}"]`)) return;
      this._cancelPendingDelete();
    };
    document.addEventListener('click', this._onDocumentClickCancelDelete);
  }

  /**
   * Переключение панелей чата (как prompt-extension).
   * @param {'main'|'settings'|'logs'} panelName
   */
  showPanel(panelName) {
    this.$.settingsPanel?.classList.add('hidden');
    this.$.logsPanel?.classList.add('hidden');
    if (panelName === 'settings') {
      this.$.settingsPanel?.classList.remove('hidden');
      this.syncPresetPromptField();
      this._hideSettingsSaveStatus();
      this.closeSidebar();
    } else if (panelName === 'logs') {
      this.$.logsPanel?.classList.remove('hidden');
      this.closeSidebar();
    } else {
      this._stopLogsLiveUpdate();
    }
  }

  _isLogsPanelOpen() {
    return this.$.logsPanel && !this.$.logsPanel.classList.contains('hidden');
  }

  openSidebar() {
    this.$.convSidebar.classList.add('open');
    this.$.backdrop.classList.remove('hidden');
    requestAnimationFrame(() => {
      this.$.backdrop.classList.add('visible');
      this._updateConvTitleTooltips();
    });
    document.body.style.overflow = 'hidden';
  }

  closeSidebar() {
    this.$.convSidebar.classList.remove('open');
    this.$.backdrop.classList.remove('visible');
    setTimeout(() => this.$.backdrop.classList.add('hidden'), 300);
    document.body.style.overflow = '';
  }

  setConnStatus(state) {
    this.$.connStatus.className = `conn-status ${state}`;
    const labels = {
      connected: 'Подключено',
      connecting: 'Подключение…',
      disconnected: 'Офлайн',
    };
    const label = labels[state] || '—';
    this.$.connStatus.title = label;
    if (this.$.connStatusLabel) this.$.connStatusLabel.textContent = label;
  }

  onAck(msg) {
    if (this._regenerating) return;
    const rows = this.$.chatMessages.querySelectorAll('.message-row.user:not([data-message-id])');
    const last = rows[rows.length - 1];
    if (last && msg.user_message_id) {
      last.dataset.messageId = msg.user_message_id;
      this._attachActions(last, 'user');
    }
  }

  async api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const res = await fetch(path, {
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch { /* ignore */ }
      const msg = typeof detail === 'string' ? detail : 'Ошибка API';
      this.log?.error('api', `${method} ${path} → ${res.status}`, msg);
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  _readPresetDrafts() {
    try {
      const raw = localStorage.getItem(PRESET_DRAFTS_STORAGE_KEY);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  _writePresetDrafts(drafts) {
    try {
      localStorage.setItem(PRESET_DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
    } catch (err) {
      this.log?.warn('settings', `Не удалось записать черновики пресетов: ${err.message}`);
    }
  }

  _flushPresetDraftsToStorage() {
    const presetId = this._editingPresetId || this.$.presetSelect?.value;
    if (presetId && this.$.presetSystemPrompt) {
      this._writePresetDraft(presetId, this.$.presetSystemPrompt.value, false);
    }
  }

  _writePresetDraft(presetId, text, synced = false) {
    if (!presetId) return;
    const drafts = this._readPresetDrafts();
    drafts[presetId] = { text, synced, updatedAt: Date.now() };
    this._writePresetDrafts(drafts);
  }

  _markPresetDraftSynced(presetId, text) {
    this._writePresetDraft(presetId, text, true);
  }

  _hasUnsyncedPresetDrafts() {
    const drafts = this._readPresetDrafts();
    return Object.values(drafts).some((d) => d && d.synced === false);
  }

  _getPresetPromptText(presetId) {
    const preset = this.presets.find((p) => p.id === presetId);
    const serverText = preset?.system_prompt ?? '';
    const draft = this._readPresetDrafts()[presetId];
    if (draft && draft.synced === false && typeof draft.text === 'string') {
      return draft.text;
    }
    return serverText;
  }

  _presetPromptDiffers(presetId, text) {
    const preset = this.presets.find((p) => p.id === presetId);
    return (preset?.system_prompt ?? '') !== text;
  }

  _isPresetPromptDirty(presetId = this._editingPresetId || this.$.presetSelect?.value) {
    if (!presetId || !this.$.presetSystemPrompt) return false;
    return this._presetPromptDiffers(presetId, this.$.presetSystemPrompt.value);
  }

  _onPresetPromptInput() {
    const presetId = this._editingPresetId || this.$.presetSelect?.value;
    if (!presetId) return;
    clearTimeout(this._presetDraftDebounceTimer);
    this._presetDraftDebounceTimer = setTimeout(() => {
      this._writePresetDraft(presetId, this.$.presetSystemPrompt.value, false);
    }, 280);
  }

  async loadPresets() {
    this.presets = await this.api('/api/presets');
    this._mergeUnsyncedPresetDrafts();
    await this._syncPendingPresetDrafts();
    const optionsHtml = this.presets
      .map((p) => `<option value="${p.id}">${this.escape(p.name)}</option>`)
      .join('');
    if (this.$.newConvPreset) {
      this.$.newConvPreset.innerHTML = optionsHtml;
      const def = this.presets.find((p) => p.is_default);
      if (def) this.$.newConvPreset.value = def.id;
    }
    this.populateGlobalPresetSelect();
    this.populateConvPresetSelect(this.currentConv?.preset_id);
  }

  _mergeUnsyncedPresetDrafts() {
    const drafts = this._readPresetDrafts();
    for (const preset of this.presets) {
      const draft = drafts[preset.id];
      if (draft && draft.synced === false && typeof draft.text === 'string') {
        preset.system_prompt = draft.text;
      }
    }
  }

  async _syncPendingPresetDrafts() {
    const drafts = this._readPresetDrafts();
    for (const preset of this.presets) {
      const draft = drafts[preset.id];
      if (!draft || draft.synced !== false || typeof draft.text !== 'string') continue;
      if (!this._presetPromptDiffers(preset.id, draft.text)) {
        this._markPresetDraftSynced(preset.id, draft.text);
        continue;
      }
      try {
        await this.savePresetPromptForId(preset.id, draft.text, { silent: true });
      } catch {
        /* черновик остаётся в localStorage */
      }
    }
  }

  populateGlobalPresetSelect() {
    if (!this.$.presetSelect || this.presets.length === 0) return;
    const stored = localStorage.getItem(PRESET_LAST_EDIT_STORAGE_KEY);
    const fallback = this.presets.find((p) => p.is_default)?.id ?? this.presets[0].id;
    const activeId = (stored && this.presets.some((p) => p.id === stored))
      ? stored
      : fallback;
    this.$.presetSelect.innerHTML = this.presets
      .map((p) => `<option value="${p.id}"${p.id === activeId ? ' selected' : ''}>${this.escape(p.name)}</option>`)
      .join('');
    this.$.presetSelect.disabled = false;
    if (this.$.presetSystemPrompt) this.$.presetSystemPrompt.disabled = false;
    this._editingPresetId = activeId;
    localStorage.setItem(PRESET_LAST_EDIT_STORAGE_KEY, activeId);
    this.syncPresetPromptField();
    this._updatePresetDefaultButton();
  }

  _chatPresetShortLabel(preset) {
    return CHAT_PRESET_SHORT_LABELS[preset.slug] ?? preset.name;
  }

  populateConvPresetSelect(selectedId) {
    if (this.presets.length === 0) return;
    const fallback = this.presets.find((p) => p.is_default)?.id ?? this.presets[0].id;
    const activeId = selectedId ?? fallback;
    const optionAttrs = (p) => `value="${p.id}"${p.id === activeId ? ' selected' : ''}`;
    const optionsHtml = this.presets
      .map((p) => `<option ${optionAttrs(p)}>${this.escape(p.name)}</option>`)
      .join('');
    const chatOptionsHtml = this.presets
      .map((p) => `<option ${optionAttrs(p)}>${this.escape(this._chatPresetShortLabel(p))}</option>`)
      .join('');
    const disabled = !this.currentConvId;
    if (this.$.convPresetSelect) {
      this.$.convPresetSelect.innerHTML = optionsHtml;
      this.$.convPresetSelect.disabled = disabled;
    }
    if (this.$.chatPresetSelect) {
      this.$.chatPresetSelect.innerHTML = chatOptionsHtml;
      this.$.chatPresetSelect.disabled = disabled;
      this.$.chatPresetSelect.title = 'Пресет для следующего сообщения';
    }
    this._updateChatPresetToolbar();
  }

  _updateChatPresetToolbar() {
    const show = Boolean(this.currentConvId) && !this.$.chatHistory?.classList.contains('hidden');
    this.$.chatPresetToolbar?.classList.toggle('hidden', !show);
  }

  async onChatPresetChange() {
    const presetId = this.$.chatPresetSelect?.value;
    if (!presetId || !this.currentConvId) return;
    if (this.$.convPresetSelect) this.$.convPresetSelect.value = presetId;
    await this._applyConversationPreset(presetId);
  }

  async _applyConversationPreset(presetId) {
    if (!this.currentConvId || this.currentConv?.preset_id === presetId) return;
    try {
      this.currentConv = await this.api(`/api/conversations/${this.currentConvId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset_id: presetId }),
      });
      if (this.$.convPresetSelect) this.$.convPresetSelect.value = presetId;
      if (this.$.chatPresetSelect) this.$.chatPresetSelect.value = presetId;
    } catch (err) {
      this.showError(err.message || 'Не удалось сменить пресет');
      this.populateConvPresetSelect(this.currentConv?.preset_id);
    }
  }

  syncPresetPromptField() {
    if (!this.$.presetSelect || !this.$.presetSystemPrompt) return;
    const presetId = this.$.presetSelect.value;
    if (!presetId) {
      this.$.presetSystemPrompt.value = '';
      this._editingPresetId = null;
      return;
    }
    this._editingPresetId = presetId;
    this.$.presetSystemPrompt.value = this._getPresetPromptText(presetId);
    this._resetPresetPromptSaveBtn();
    this._updatePresetDefaultButton();
  }

  _updatePresetDefaultButton() {
    const btn = this.$.presetSetDefaultBtn;
    const presetId = this.$.presetSelect?.value;
    if (!btn || !presetId) {
      if (btn) btn.disabled = true;
      return;
    }
    const preset = this.presets.find((p) => p.id === presetId);
    btn.disabled = Boolean(preset?.is_default);
    btn.textContent = preset?.is_default
      ? 'Пресет по умолчанию'
      : 'Сделать пресетом по умолчанию';
  }

  async onPresetSelectChange() {
    const oldId = this._editingPresetId;
    const newId = this.$.presetSelect?.value;
    if (oldId && oldId !== newId && this.$.presetSystemPrompt) {
      const text = this.$.presetSystemPrompt.value;
      this._writePresetDraft(oldId, text, false);
      if (this._presetPromptDiffers(oldId, text)) {
        try {
          await this.savePresetPromptForId(oldId, text, { silent: true });
        } catch (err) {
          this._showSettingsSaveStatus('error', err.message || 'Не удалось сохранить пресет');
          this.$.presetSelect.value = oldId;
          return;
        }
      }
    }
    if (newId) localStorage.setItem(PRESET_LAST_EDIT_STORAGE_KEY, newId);
    this._editingPresetId = newId;
    this.syncPresetPromptField();
    this._hideSettingsSaveStatus();
  }

  _resetPresetPromptSaveBtn() {
    const btn = this.$.presetPromptSaveBtn;
    if (!btn) return;
    clearTimeout(this._presetPromptSaveBtnTimer);
    btn.disabled = false;
    btn.setAttribute('aria-label', 'Сохранить промпт на сервер');
    btn.classList.remove('is-success', 'is-error', 'is-saving');
  }

  async savePresetPrompt(options = {}) {
    const presetId = this.$.presetSelect?.value;
    if (!presetId || !this.$.presetSystemPrompt) return false;
    return this.savePresetPromptForId(
      presetId,
      this.$.presetSystemPrompt.value,
      options,
    );
  }

  async savePresetPromptIfDirty(options = {}) {
    if (!this._isPresetPromptDirty()) return true;
    return this.savePresetPrompt(options);
  }

  async savePresetPromptForId(presetId, text, { silent = false } = {}) {
    const btn = this.$.presetPromptSaveBtn;
    if (!presetId) return false;

    if (btn) {
      btn.disabled = true;
      btn.classList.remove('is-success', 'is-error');
      btn.classList.add('is-saving');
      btn.setAttribute('aria-label', 'Сохранение…');
    }

    try {
      const updated = await this.api(`/api/presets/${presetId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ system_prompt: text }),
      });
      const idx = this.presets.findIndex((p) => p.id === presetId);
      if (idx >= 0) this.presets[idx] = updated;
      this._markPresetDraftSynced(presetId, text);
      if (btn) {
        btn.classList.remove('is-saving');
        btn.classList.add('is-success');
        btn.setAttribute('aria-label', 'Сохранено');
      }
      this.log?.info('settings', `Глобальный промпт пресета ${presetId} сохранён`);
      return true;
    } catch (err) {
      this._writePresetDraft(presetId, text, false);
      if (btn) {
        btn.classList.remove('is-saving');
        btn.classList.add('is-error');
        btn.setAttribute('aria-label', 'Ошибка сохранения');
      }
      if (!silent) {
        this._showSettingsSaveStatus('error', err.message || 'Не удалось сохранить промпт');
      }
      throw err;
    } finally {
      if (btn) {
        btn.disabled = false;
        clearTimeout(this._presetPromptSaveBtnTimer);
        this._presetPromptSaveBtnTimer = setTimeout(() => {
          btn.classList.remove('is-success', 'is-error', 'is-saving');
          btn.setAttribute('aria-label', 'Сохранить промпт на сервер');
        }, 2200);
      }
    }
  }

  async setDefaultPreset() {
    const presetId = this.$.presetSelect?.value;
    const btn = this.$.presetSetDefaultBtn;
    if (!presetId || !btn) return;
    btn.disabled = true;
    try {
      await this.savePresetPromptIfDirty({ silent: true });
      await this.api(`/api/presets/${presetId}/set-default`, { method: 'POST' });
      await this.loadPresets();
      this.$.presetSelect.value = presetId;
      this._editingPresetId = presetId;
      this.syncPresetPromptField();
      this.log?.info('settings', `Пресет ${presetId} — по умолчанию`);
    } catch (err) {
      this._showSettingsSaveStatus('error', err.message || 'Не удалось обновить пресет');
    } finally {
      this._updatePresetDefaultButton();
    }
  }

  async loadConversations() {
    this.conversations = await this.api('/api/conversations');
    this.renderConvList();
  }

  renderConvList() {
    this._cancelPendingDelete();
    const empty = !this.conversations.length;
    this.$.convEmpty.classList.toggle('hidden', !empty);

    const newChatRow = this.$.convList.querySelector('.conv-new-item');
    const convItemsHtml = this.conversations
      .map((c) => {
        const active = c.id === this.currentConvId ? ' active' : '';
        const date = new Date(c.updated_at).toLocaleString('ru-RU', {
          day: '2-digit',
          month: 'short',
          hour: '2-digit',
          minute: '2-digit',
        });
        return `<li class="conv-item${active}" data-id="${c.id}" role="listitem">
          <div class="conv-item-row">
            <div class="conv-item-main">
              <div class="conv-item-title" data-id="${c.id}" aria-label="${this.escapeAttr(c.title)}">
                <span class="conv-item-title-text">${this.escape(c.title)}</span>
                <span class="conv-item-title-tooltip" role="tooltip" aria-hidden="true">
                  <span class="conv-item-title-tooltip-body">${this.escape(c.title)}</span>
                  <span class="conv-item-title-tooltip-hint">Двойной клик — переименовать</span>
                </span>
              </div>
              <div class="conv-item-meta">${date}</div>
            </div>
            <button type="button" class="conv-item-delete" data-id="${c.id}" title="Удалить беседу" aria-label="Удалить беседу">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </li>`;
      })
      .join('');

    this.$.convList.innerHTML = '';
    if (newChatRow) {
      this.$.convList.appendChild(newChatRow);
    }
    this.$.convList.insertAdjacentHTML('beforeend', convItemsHtml);

    this.$.convList.querySelectorAll('.conv-item').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.conv-item-delete')) return;
        this.selectConversation(el.dataset.id);
        this.closeSidebar();
      });
    });

    this.$.convList.querySelectorAll('.conv-item-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._onDeleteBtnClick(btn.dataset.id, btn);
      });
    });

    this._bindConvTitleInlineEdit();
    this._updateConvTitleTooltips();
  }

  _updateConvTitleTooltips() {
    this.$.convList.querySelectorAll('.conv-item-title').forEach((el) => {
      const textEl = el.querySelector('.conv-item-title-text');
      if (!textEl) return;
      const truncated = textEl.scrollWidth > textEl.clientWidth + 1;
      el.classList.toggle('is-truncated', truncated);
    });
  }

  _bindConvTitleInlineEdit() {
    this.$.convList.querySelectorAll('.conv-item-title').forEach((el) => {
      el.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        const convId = el.dataset.id || el.closest('.conv-item')?.dataset.id;
        if (convId) this._startInlineTitleEdit(convId, el);
      });
    });
  }

  async _startInlineTitleEdit(convId, titleEl) {
    if (this._inlineTitleConvId) return;
    const conv = this.conversations.find((c) => c.id === convId);
    if (!conv) return;

    this._inlineTitleConvId = convId;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'conv-item-title-input';
    input.value = conv.title;
    input.maxLength = 200;
    input.setAttribute('aria-label', 'Название беседы');

    const finish = async (save) => {
      input.removeEventListener('blur', onBlur);
      input.removeEventListener('keydown', onKey);
      const next = input.value.trim() || 'Новая беседа';
      const textEl = titleEl.querySelector('.conv-item-title-text');
      const tipBody = titleEl.querySelector('.conv-item-title-tooltip-body');
      if (textEl) textEl.textContent = conv.title;
      else titleEl.textContent = conv.title;
      if (tipBody) tipBody.textContent = conv.title;
      titleEl.setAttribute('aria-label', conv.title);
      titleEl.classList.remove('hidden');
      input.remove();
      this._inlineTitleConvId = null;
      this._updateConvTitleTooltips();
      if (save && next !== conv.title) {
        await this._patchConversationTitle(convId, next);
      }
    };

    const onBlur = () => { void finish(true); };
    const onKey = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        void finish(true);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        void finish(false);
      }
    };

    titleEl.classList.add('hidden');
    titleEl.after(input);
    input.addEventListener('blur', onBlur);
    input.addEventListener('keydown', onKey);
    input.focus();
    input.select();
  }

  async _patchConversationTitle(convId, title) {
    try {
      const updated = await this.api(`/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      const conv = this.conversations.find((c) => c.id === convId);
      if (conv) conv.title = updated.title;
      if (this.currentConvId === convId) {
        this.currentConv = updated;
        this._setSettingsChatTitle(updated.title);
      }
      this.renderConvList();
    } catch (err) {
      this.showError(err.message);
    }
  }

  _onConvSearchInput() {
    clearTimeout(this._searchDebounceTimer);
    const q = this.$.convSearch?.value?.trim() ?? '';
    if (!q) {
      this._clearConvSearch();
      return;
    }
    this._searchDebounceTimer = setTimeout(() => {
      void this._runConvSearch(q);
    }, 300);
  }

  _isConvSearchOpen() {
    return this.$.convSearchStack?.classList.contains('is-open') ?? false;
  }

  _openConvSearchPanel() {
    const stack = this.$.convSearchStack;
    if (!stack) return;
    stack.hidden = false;
    stack.setAttribute('aria-hidden', 'false');
    stack.classList.add('is-open');
    this.$.convSearchToggle?.classList.add('is-active');
    this.$.convSearchToggle?.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => this.$.convSearch?.focus());
  }

  _closeConvSearchPanel() {
    const stack = this.$.convSearchStack;
    if (!stack || !this._isConvSearchOpen()) return;
    stack.classList.remove('is-open');
    this.$.convSearchToggle?.classList.remove('is-active');
    this.$.convSearchToggle?.setAttribute('aria-expanded', 'false');
    const hideStack = () => {
      if (!stack.classList.contains('is-open')) {
        stack.hidden = true;
        stack.setAttribute('aria-hidden', 'true');
      }
    };
    stack.addEventListener('transitionend', hideStack, { once: true });
    setTimeout(hideStack, 280);
    this._clearConvSearch();
  }

  _toggleConvSearchPanel() {
    if (this._isConvSearchOpen()) {
      this._closeConvSearchPanel();
    } else {
      this._openConvSearchPanel();
    }
  }

  _clearConvSearch() {
    if (this.$.convSearch) this.$.convSearch.value = '';
    this.$.convSearchResults?.classList.add('hidden');
    if (this.$.convSearchResults) this.$.convSearchResults.innerHTML = '';
  }

  async _runConvSearch(q) {
    if (!this.$.convSearchResults) return;
    try {
      const hits = await this.api(`/api/search?q=${encodeURIComponent(q)}`);
      this._renderSearchResults(hits, q);
    } catch (err) {
      this.showError(err.message);
    }
  }

  _renderSearchResults(hits, q) {
    const el = this.$.convSearchResults;
    if (!el) return;
    el.classList.remove('hidden');
    if (!hits.length) {
      el.innerHTML = '<li class="conv-search-empty" role="listitem">Ничего не найдено</li>';
      return;
    }
    el.innerHTML = hits
      .map((h) => {
        const kindLabel = h.match_kind === 'title' ? 'Название' : 'Сообщение';
        const msgId = h.message_id || '';
        return `<li class="conv-search-hit" role="listitem" tabindex="0"
          data-conv-id="${h.conversation_id}" data-message-id="${msgId}">
          <div class="conv-search-hit-title">${this.escape(h.conversation_title)}</div>
          <div class="conv-search-hit-meta">
            <span class="conv-search-hit-kind">${kindLabel}</span>
            <div class="conv-search-hit-snippet">${this._highlightSearchSnippet(h.snippet, q)}</div>
          </div>
        </li>`;
      })
      .join('');

    el.querySelectorAll('.conv-search-hit').forEach((item) => {
      const open = () => {
        void this._openSearchHit(
          item.dataset.convId,
          item.dataset.messageId || null,
        );
      };
      item.addEventListener('click', open);
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') open();
      });
    });
  }

  _highlightSearchSnippet(snippet, query) {
    let html = this.escape(snippet);
    const words = query.trim().split(/\s+/).filter(Boolean);
    for (const word of words) {
      if (!word) continue;
      const re = new RegExp(`(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
  }

  async _openSearchHit(conversationId, messageId) {
    this._closeConvSearchPanel();
    this.closeSidebar();
    await this.selectConversation(conversationId);
    if (messageId) this._highlightMessage(messageId);
  }

  _highlightMessage(messageId) {
    const row = this._findMessageRow(messageId);
    if (!row) return;
    row.classList.add('search-highlight');
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => row.classList.remove('search-highlight'), 2600);
  }

  exportCurrentConversation() {
    if (!this.currentConvId) return;
    window.location.assign(`/api/conversations/${this.currentConvId}/export`);
  }

  _updateExportButton() {
    if (!this.$.exportConversationBtn) return;
    this.$.exportConversationBtn.disabled = !this.currentConvId;
  }

  _onDeleteBtnClick(id, btn) {
    if (this._pendingDeleteConvId === id) {
      void this._executeDeleteConversation(id);
      return;
    }
    this._cancelPendingDelete();
    this._pendingDeleteConvId = id;
    this._pendingDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.closest('.conv-item')?.classList.add('delete-pending');
    btn.title = 'Нажмите ещё раз для удаления';
  }

  _cancelPendingDelete() {
    if (!this._pendingDeleteConvId) return;
    this._pendingDeleteBtn?.classList.remove('delete-armed');
    this._pendingDeleteBtn?.closest('.conv-item')?.classList.remove('delete-pending');
    if (this._pendingDeleteBtn) {
      this._pendingDeleteBtn.title = 'Удалить беседу';
    }
    this._pendingDeleteConvId = null;
    this._pendingDeleteBtn = null;
  }

  async _executeDeleteConversation(id) {
    this._cancelPendingDelete();
    this.log?.info('chat', `Удаление беседы ${id}`);
    try {
      await this.api(`/api/conversations/${id}`, { method: 'DELETE' });
      if (this.currentConvId === id) {
        this._clearCurrentConversation();
      }
      await this.loadConversations();
    } catch (err) {
      this.showError(err.message);
    }
  }

  _clearCurrentConversation() {
    this.disconnectSocket();
    this.currentConvId = null;
    this.currentConv = null;
    localStorage.removeItem('webchat_conv_id');
    this.showPanel('main');
    this._setSettingsChatTitle(null);
    this.populateConvPresetSelect();
    this.$.placeholder.classList.remove('hidden');
    this.$.chatHistory.classList.add('hidden');
    this.$.chatPresetToolbar?.classList.add('hidden');
    this.$.chatComposer.classList.add('hidden');
    this.$.chatMessages.innerHTML = '';
    this.$.userInput.value = '';
    this.$.userInput.disabled = true;
    this.$.sendBtn.disabled = true;
    if (this.$.macroInsertBtn) this.$.macroInsertBtn.disabled = true;
    this.clearAttachments();
    if (this.streaming) this.endStreaming();
  }

  openNewConvModal() {
    document.getElementById('new-conv-title').value = '';
    this.$.newConvModal.showModal();
    setTimeout(() => document.getElementById('new-conv-title').focus(), 50);
  }

  async createConversation() {
    const title = document.getElementById('new-conv-title').value.trim() || 'Новая беседа';
    const presetId = this.$.newConvPreset.value || null;
    const body = { title };
    if (presetId) body.preset_id = presetId;
    const conv = await this.api('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    this.$.newConvModal.close();
    await this.loadConversations();
    await this.selectConversation(conv.id);
  }

  async selectConversation(id) {
    const rs = this.socket?.ws?.readyState;
    if (
      this.currentConvId === id
      && (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING)
    ) {
      return;
    }

    this._cancelPendingDelete();
    this._closeConvSearchPanel();
    this.disconnectSocket();
    this.log?.info('chat', `Беседа ${id}`);
    this.currentConvId = id;
    localStorage.setItem('webchat_conv_id', id);

    this.$.loadingOverlay.classList.remove('hidden');
    try {
      this.currentConv = await this.api(`/api/conversations/${id}`);

      this._setSettingsChatTitle(this.currentConv.title);
      this.populateConvPresetSelect(this.currentConv.preset_id);

      this.$.placeholder.classList.add('hidden');
      this.$.chatHistory.classList.remove('hidden');
      this._updateChatPresetToolbar();
      this.$.chatComposer.classList.remove('hidden');
      this.$.userInput.disabled = false;
      this.$.sendBtn.disabled = false;
      if (this.$.macroInsertBtn) this.$.macroInsertBtn.disabled = false;
      this.autoResizeInput();

    await this.loadMessages();
    await this._resumeOngoingGeneration();
    this._restorePendingAttachmentsFromSession();
    this._scrollStuckToBottom = true;
    this.renderConvList();
    this.connectSocket();
    } finally {
      this.$.loadingOverlay.classList.add('hidden');
    }
  }

  async loadMessages() {
    const messages = await this.api(`/api/conversations/${this.currentConvId}/messages?limit=100`);
    this.$.chatMessages.innerHTML = '';
    for (const m of messages) {
      this.appendMessageFromDb(m);
    }
    this.scrollToBottom(true);
  }

  appendMessageFromDb(m) {
    const urls = imageUrlsFromMessage(m);
    if (m.role === 'user') {
      this.addUserBubble(m.content_text || '', m.id, urls);
    } else if (m.role === 'assistant' && m.content_json?.streaming) {
      this.appendAssistantDraftFromDb(m, urls);
    } else if (m.role === 'assistant') {
      this.addAssistantBubble(m.content_text || '', urls, m.id);
    }
  }

  _ensureAssistantStreamShell(el) {
    if (!el.querySelector('.message-status')) {
      el.insertAdjacentHTML('afterbegin', MESSAGE_STATUS_HTML);
    }
    let bubble = el.querySelector('.message-bubble');
    if (!bubble) {
      bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      const images = el.querySelector('.message-images');
      if (images) {
        el.insertBefore(bubble, images);
      } else {
        el.appendChild(bubble);
      }
    }
    if (!el.querySelector('.message-images')) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      el.appendChild(grid);
    }
    return el;
  }

  _fillAssistantBubble(el, text, imageUrls) {
    const displayText = stripMarkdownImages(text || '');
    const bubble = el.querySelector('.message-bubble');
    const grid = el.querySelector('.message-images');
    el.dataset.rawContent = text || '';
    if (displayText && bubble) {
      bubble.innerHTML = formatMarkdown(displayText);
      el.classList.add('has-content');
    } else if (bubble) {
      bubble.innerHTML = '';
      el.classList.remove('has-content');
    }
    if (grid) {
      this._setGridImages(grid, imageUrls || []);
      el.classList.toggle('has-images', grid.children.length > 0);
    }
    this._bindImageClicks(el);
  }

  appendAssistantDraftFromDb(m, imageUrls = null) {
    const urls = imageUrls ?? imageUrlsFromMessage(m);
    const el = document.createElement('div');
    el.className = 'chat-message assistant';
    this._ensureAssistantStreamShell(el);
    this._fillAssistantBubble(el, m.content_text || '', urls);
    const row = this._wrapMessage('assistant', el, m.id);
    row.dataset.streamingDraft = 'true';
    this.$.chatMessages.appendChild(row);
  }

  connectSocket() {
    if (this.socket) {
      this.disconnectSocket();
    }
    this.setConnStatus('connecting');
    this.log?.info('ws', `Подключение к беседе ${this.currentConvId}`);
    this.socket = new ChatSocket(this.currentConvId, {
      onConnecting: () => this.setConnStatus('connecting'),
      onOpen: () => {
        this.setConnStatus('connected');
        this.log?.info('ws', 'Соединение установлено');
      },
      onClose: () => {
        if (!this.streaming && !this._generationResumeActive) {
          this.setConnStatus('disconnected');
        }
        this.log?.warn('ws', 'Соединение закрыто');
      },
      onReconnecting: (delay) => {
        if (this.streaming || this._generationResumeActive) return;
        this.setConnStatus('connecting');
        this.log?.warn('ws', `Переподключение через ${delay} мс`);
      },
      onError: () => this.log?.error('ws', 'Ошибка WebSocket'),
      onTextDelta: (chunk) => this.onTextDelta(chunk),
      onImages: (urls) => this.onImages(urls),
      onToolStart: (name) => this.onToolStart(name),
      onToolDone: () => this.onToolDone(),
      onAck: (msg) => this.onAck(msg),
      onDone: (msg) => this.onTurnDone(msg),
      onWsError: (message, code) => this.onWsError(message, code),
      onConnected: (msg) => this._onWsConnected(msg),
      onAssistantDraft: (msg) => this._onAssistantDraft(msg),
    });
    this.socket.connect();
  }

  disconnectSocket() {
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.socket?.disconnect();
    this.socket = null;
    this.setConnStatus('disconnected');
  }

  async sendMessage() {
    const text = this.$.userInput.value.trim();
    if (!text || this.streaming) return;
    if (!this.socket) return;

    if (this.editingMessageId) {
      await this._submitEdit(text);
      return;
    }

    const ids = this.pendingAttachments.map((a) => a.id);
    const pendingImages = this.pendingAttachments
      .map((a) => resolveMediaUrl(a.preview_url))
      .filter(Boolean);
    this.addUserBubble(text, null, pendingImages);
    this.$.userInput.value = '';
    this.autoResizeInput();
    this.clearAttachments();
    this.startStreaming();

    try {
      this.socket.sendUserMessage(text, ids, this.getWsIntegrationPayload());
    } catch (err) {
      this.showError(err.message);
      this.endStreaming();
    }
  }

  async _submitEdit(text) {
    const messageId = this.editingMessageId;
    const role = this.editingRole || 'user';
    this.editingMessageId = null;
    this.editingRole = null;
    this.$.userInput.value = '';
    this.$.userInput.placeholder = this._inputPlaceholderDefault;
    this.autoResizeInput();

    const row = this._findRow(messageId);
    try {
      this.log?.info('msg', `Редактирование ${role} ${messageId}`);
      await this.api(`/api/conversations/${this.currentConvId}/messages/${messageId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_text: text }),
      });

      if (role === 'user') {
        if (row) {
          const textEl = row.querySelector('.user-text');
          if (textEl) {
            textEl.dataset.rawText = text;
            this.promptMacros.renderUserText(textEl, text);
          }
          this._removeFollowingRows(row, false);
        }
        await this._runRegenerate(messageId);
      } else if (row) {
        const mb = row.querySelector('.message-bubble');
        if (mb) mb.innerHTML = formatMarkdown(text);
        this._bindImageClicks(row);
      }
    } catch (err) {
      this.showError(err.message);
      if (/не найдено|not found/i.test(err.message)) {
        this.log?.warn('msg', 'Сообщение не найдено на сервере — перезагрузка истории');
        await this.loadMessages();
      }
    }
  }

  cancelGeneration() {
    this.socket?.cancel();
    this.showError('Отмена запроса…', 3000);
  }

  startStreaming() {
    this.streaming = true;
    this.streamText = '';
    this.$.sendBtn.classList.add('hidden');
    this.$.sendBtn.disabled = true;
    this.$.cancelBtn.classList.remove('hidden');

    const el = document.createElement('div');
    el.className = 'chat-message assistant streaming waiting';
    el.innerHTML = `
      ${MESSAGE_STATUS_HTML}
      <div class="message-bubble"></div>
      <div class="message-images"></div>
    `;
    const content = document.createElement('div');
    content.className = 'message-content';
    content.appendChild(el);
    const row = document.createElement('div');
    row.className = 'message-row assistant';
    row.dataset.temp = 'true';
    row.appendChild(content);
    this.$.chatMessages.appendChild(row);
    this.streamRow = row;
    this.streamEl = el;
    this.streamImagesEl = el.querySelector('.message-images');
    this.showProgress('Обработка запроса');
    this.scrollToBottom(true);
  }

  onTextDelta(chunk) {
    if (!this._ensureStreamTarget()) return;
    this.streamText += chunk;
    if (this.streamEl.dataset) {
      this.streamEl.dataset.rawContent = this.streamText;
    }
    this.hideProgress();
    this.streamEl.classList.remove('waiting');
    const bubble = this.streamEl.querySelector('.message-bubble');
    const displayText = stripMarkdownImages(this.streamText);
    if (displayText) {
      this.streamEl.classList.add('has-content');
      bubble.innerHTML = formatMarkdown(displayText);
    } else {
      this.streamEl.classList.remove('has-content');
      bubble.innerHTML = '';
    }
    bubble.querySelectorAll('img').forEach((img) => {
      const src = img.getAttribute('src');
      if (src) img.src = resolveMediaUrl(src);
    });
    this.scrollToBottom();
  }

  onImages(urls) {
    if (!this._ensureStreamTarget() || !this.streamImagesEl) return;
    const added = this._appendImagesToGrid(this.streamImagesEl, urls);
    if (added > 0) {
      this.hideProgress();
      this.streamEl?.classList.add('has-images');
    }
    this.scrollToBottom();
  }

  onToolStart(name) {
    this._ensureStreamTarget();
    this.showProgress(TOOL_PROGRESS_LABELS[name] || `Выполняется: ${name}`);
    this.scrollToBottom();
  }

  onToolDone() {
    if (
      this.streamEl
      && !this.streamText
      && !this.streamImagesEl?.children.length
    ) {
      this.showProgress('Формирую ответ');
    } else {
      this.hideProgress();
    }
  }

  _clearGenerationSyncTimer() {
    if (this._generationSyncTimer) {
      clearTimeout(this._generationSyncTimer);
      this._generationSyncTimer = null;
    }
    this._generationWatchRunning = false;
  }

  _onWsConnected(msg) {
    const inProgress = Boolean(msg?.in_progress || msg?.generation_in_progress);
    if (inProgress) {
      void this._resumeOngoingGeneration(msg);
    } else if (!this._generationWatchRunning) {
      this._generationResumeActive = false;
    }
  }

  _onAssistantDraft(msg) {
    const id = msg?.assistant_message_id;
    if (!id) return;
    this._generationResumeActive = true;
    const tempRow = this.$.chatMessages.querySelector(
      '.message-row.assistant[data-temp="true"]',
    );
    if (tempRow) {
      tempRow.removeAttribute('data-temp');
      tempRow.dataset.messageId = id;
      this._applyStreamUI(tempRow);
      this._attachActions(tempRow, 'assistant');
      return;
    }
    if (!this._bindStreamToMessageId(id)) {
      void this.loadMessages().then(() => this._bindStreamToMessageId(id));
    }
  }

  _applyStreamUI(row) {
    this.streaming = true;
    this.streamRow = row;
    this.streamEl = row.querySelector('.chat-message.assistant');
    if (!this.streamEl) return;
    row.removeAttribute('data-streaming-draft');
    this._ensureAssistantStreamShell(this.streamEl);
    this.streamEl.classList.add('streaming');
    this.streamImagesEl = this.streamEl.querySelector('.message-images');
    this.streamText = this.streamEl.dataset.rawContent || '';
    const bubble = this.streamEl.querySelector('.message-bubble');
    const hasImages = Boolean(this.streamImagesEl?.children.length);
    if (this.streamText && bubble) {
      const displayText = stripMarkdownImages(this.streamText);
      bubble.innerHTML = formatMarkdown(displayText);
      this.streamEl.classList.toggle('has-content', Boolean(displayText));
    }
    this.streamEl.classList.toggle('has-images', hasImages);
    this.$.sendBtn.classList.add('hidden');
    this.$.sendBtn.disabled = true;
    this.$.cancelBtn.classList.remove('hidden');
  }

  _bindStreamToMessageId(messageId) {
    if (!messageId) return false;
    const row = this._findRow(messageId);
    if (!row) return false;
    this._applyStreamUI(row);
    if (row.dataset.messageId) {
      this._attachActions(row, 'assistant');
    }
    return true;
  }

  _beginResumePlaceholder() {
    if (this.streamEl) return;
    this.startStreaming();
  }

  _syncResumeProgress(status = {}) {
    if (!this.streamEl) return;
    const hasText = Boolean(stripMarkdownImages(this.streamText || ''));
    const hasImages = Boolean(this.streamImagesEl?.children.length);
    if (status.phase === 'tool' && status.active_tool) {
      const label = TOOL_PROGRESS_LABELS[status.active_tool]
        || `Выполняется: ${status.active_tool}`;
      this.showProgress(label);
      return;
    }
    if (hasText && !hasImages && status.in_progress) {
      this.showProgress('Дописываю ответ');
      return;
    }
    if (hasText || hasImages) {
      this.hideProgress();
      return;
    }
    this.showProgress('Продолжается генерация');
  }

  async _resumeOngoingGeneration(serverMsg = {}) {
    if (!this.currentConvId) return;
    if (this._generationWatchRunning && this.streamEl) {
      return;
    }

    let status;
    try {
      status = await this.api(
        `/api/conversations/${this.currentConvId}/generation-status`,
      );
    } catch (err) {
      this.log?.warn('chat', err.message);
      return;
    }

    if (!status.in_progress) {
      this._generationResumeActive = false;
      if (this.streaming) {
        await this.loadMessages();
        this.endStreaming();
      }
      return;
    }

    this._generationResumeActive = true;
    this.log?.info('chat', 'Подключение к идущей генерации на сервере');

    const streamId = status.streaming_message_id || serverMsg?.streaming_message_id;
    let bound = false;

    if (streamId) {
      bound = this._bindStreamToMessageId(streamId);
    }
    if (!bound) {
      const draftRow = this.$.chatMessages.querySelector(
        '.message-row.assistant[data-streaming-draft="true"]',
      );
      if (draftRow?.dataset?.messageId) {
        bound = this._bindStreamToMessageId(draftRow.dataset.messageId);
      }
    }
    if (!bound) {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const draft = [...messages]
        .reverse()
        .find((m) => m.role === 'assistant' && m.content_json?.streaming);
      if (draft) {
        bound = this._bindStreamToMessageId(draft.id);
      }
      if (!bound && messages[messages.length - 1]?.role === 'user') {
        this._beginResumePlaceholder();
        if (streamId && this.streamRow) {
          this.streamRow.dataset.messageId = streamId;
        }
      }
    }

    this._syncResumeProgress(status);
    await this._refreshStreamingBubbleFromServer();
    this._watchGenerationUntilDone();
  }

  _ensureStreamTarget() {
    if (this.streamEl) return true;
    if (!this._generationResumeActive) return false;
    this._beginResumePlaceholder();
    return Boolean(this.streamEl);
  }

  async _refreshStreamingBubbleFromServer() {
    if (!this.currentConvId) return;
    const targetId = this.streamRow?.dataset?.messageId;
    const messages = await this.api(
      `/api/conversations/${this.currentConvId}/messages?limit=50`,
    );

    let target = null;
    if (targetId) {
      target = messages.find((m) => m.id === targetId);
    }
    if (!target) {
      target = [...messages]
        .reverse()
        .find((m) => m.role === 'assistant' && m.content_json?.streaming);
    }
    if (!target) return;

    if (!this.streamEl || (targetId && target.id !== targetId)) {
      this._bindStreamToMessageId(target.id);
    }
    if (!this.streamEl) return;

    const newText = target.content_text || '';
    if (newText !== this.streamText) {
      this.streamText = newText;
      this.streamEl.dataset.rawContent = newText;
      const bubble = this.streamEl.querySelector('.message-bubble');
      if (bubble && newText) {
        const displayText = stripMarkdownImages(newText);
        bubble.innerHTML = formatMarkdown(displayText);
        this.streamEl.classList.add('has-content');
        this.hideProgress();
      }
    }

    const urls = imageUrlsFromMessage(target);
    if (urls.length && this.streamImagesEl) {
      this._setGridImages(this.streamImagesEl, urls);
      if (this.streamImagesEl.children.length) {
        this.streamEl.classList.add('has-images');
      }
    }

    if (target.id && this.streamRow) {
      this.streamRow.dataset.messageId = target.id;
      this.streamRow.removeAttribute('data-streaming-draft');
      this._attachActions(this.streamRow, 'assistant');
    }

    const hasText = Boolean(stripMarkdownImages(this.streamText || ''));
    const hasImages = Boolean(this.streamImagesEl?.children.length);
    if (hasImages || hasText) {
      this.streamEl.classList.toggle('has-content', hasText);
      this.streamEl.classList.toggle('has-images', hasImages);
    }
  }

  _watchGenerationUntilDone() {
    if (this._generationWatchRunning) return;
    this._generationWatchRunning = true;
    this._clearGenerationSyncTimer();
    const poll = async () => {
      if (!this.currentConvId || !this._generationResumeActive) {
        this._generationWatchRunning = false;
        return;
      }
      try {
        const st = await this.api(
          `/api/conversations/${this.currentConvId}/generation-status`,
        );
        if (!st.in_progress) {
          this._generationResumeActive = false;
          this._generationWatchRunning = false;
          await this.loadMessages();
          this.endStreaming();
          return;
        }
        await this._refreshStreamingBubbleFromServer();
        this._syncResumeProgress(st);
        this._generationSyncTimer = setTimeout(poll, 2000);
      } catch (err) {
        this._generationResumeActive = false;
        this._generationWatchRunning = false;
        this.log?.warn('chat', err.message);
      }
    };
    poll();
  }

  onTurnDone(msg) {
    const assistantMessageId = msg?.assistant_message_id;
    const conversationTitle = msg?.conversation_title;
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.hideProgress();
    if (this.streamRow && assistantMessageId) {
      this.streamRow.dataset.messageId = assistantMessageId;
      this._attachActions(this.streamRow, 'assistant');
    }
    this._regenerating = false;
    this.endStreaming();
    if (conversationTitle && this.currentConv) {
      this.currentConv.title = conversationTitle;
      const conv = this.conversations.find((c) => c.id === this.currentConvId);
      if (conv) conv.title = conversationTitle;
      this._setSettingsChatTitle(conversationTitle);
    }
    this.loadConversations();
  }

  onWsError(message, code) {
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.hideProgress();
    this.log?.error('ws', `Ошибка генерации (${code || 'unknown'})`, message);
    if (code === 'tool_loop' && this.currentConvId) {
      this.loadMessages().catch(() => {});
    }
    if (code !== 'cancelled') {
      this.showError(message || 'Ошибка генерации');
    } else {
      this.showError(message || 'Генерация отменена', 4000);
    }
    if (this.streamRow && !this.streamText) {
      this.streamRow.remove();
    }
    this.endStreaming();
    this._regenerating = false;
  }

  endStreaming() {
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.streaming = false;
    this.$.sendBtn.classList.remove('hidden', 'loading');
    this.$.sendBtn.disabled = false;
    this.$.cancelBtn.classList.add('hidden');
    if (!this.$.userInput.disabled) {
      this.$.userInput.focus();
    }

    if (this.streamEl) {
      this.streamEl.classList.remove('streaming', 'waiting', 'is-busy', 'has-content', 'has-images');
      if (!this.streamText && !this.streamImagesEl?.children.length) {
        this.streamRow?.remove();
      }
      this.streamEl = null;
      this.streamRow = null;
      this.streamImagesEl = null;
    }
  }

  _findRow(messageId) {
    return this.$.chatMessages.querySelector(`.message-row[data-message-id="${messageId}"]`);
  }

  _removeFollowingRows(fromRow, includeFrom) {
    let node = includeFrom ? fromRow : fromRow?.nextElementSibling;
    while (node) {
      const next = node.nextElementSibling;
      node.remove();
      node = next;
    }
  }

  _buildActions() {
    const bar = document.createElement('div');
    bar.className = 'message-actions';
    for (const a of [
      { key: 'copy', title: 'Скопировать текст', icon: MSG_ICONS.copy },
      { key: 'edit', title: 'Редактировать', icon: MSG_ICONS.edit },
      { key: 'regenerate', title: 'Перегенерировать', icon: MSG_ICONS.regen },
      { key: 'delete', title: 'Удалить', icon: MSG_ICONS.delete, danger: true },
    ]) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `msg-action-btn${a.danger ? ' danger' : ''}`;
      btn.dataset.action = a.key;
      btn.title = a.title;
      btn.innerHTML = a.icon;
      bar.appendChild(btn);
    }
    return bar;
  }

  _attachActions(row, role) {
    if (row.querySelector('.message-actions')) return;
    const bar = this._buildActions();
    bar.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn || !row.dataset.messageId) return;
      const id = row.dataset.messageId;
      if (btn.dataset.action === 'copy') this.copyMessageText(id, role, btn);
      else if (btn.dataset.action === 'delete') this.deleteMessage(id, role);
      else if (btn.dataset.action === 'edit') this.editMessage(id, role);
      else if (btn.dataset.action === 'regenerate') this.regenerateMessage(id);
    });
    row.appendChild(bar);
  }

  _wrapMessage(role, messageEl, messageId) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;
    if (messageId) row.dataset.messageId = messageId;
    const content = document.createElement('div');
    content.className = 'message-content';
    content.appendChild(messageEl);
    row.appendChild(content);
    if (messageId) this._attachActions(row, role);
    return row;
  }

  addUserBubble(text, messageId = null, imageUrls = []) {
    const el = document.createElement('div');
    el.className = 'chat-message user';
    if (text) {
      const textEl = document.createElement('div');
      textEl.className = 'user-text';
      textEl.dataset.rawText = text;
      this.promptMacros.renderUserText(textEl, text);
      el.appendChild(textEl);
    }
    const urls = [...new Set(imageUrls.map(resolveMediaUrl))];
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(this._createImage(url));
      el.appendChild(grid);
      this._bindImageClicks(el);
    }
    this.$.chatMessages.appendChild(this._wrapMessage('user', el, messageId));
    this.scrollToBottom();
  }

  addAssistantBubble(text, imageUrls, messageId = null) {
    const el = document.createElement('div');
    el.className = 'chat-message assistant';
    el.dataset.rawContent = text || '';
    const displayText = stripMarkdownImages(text);
    const urls = [...new Set(imageUrls.map(resolveMediaUrl).filter(Boolean))];
    if (displayText) {
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.innerHTML = formatMarkdown(displayText);
      el.appendChild(bubble);
    }
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(this._createImage(url));
      el.appendChild(grid);
    }
    this._bindImageClicks(el);
    this.$.chatMessages.appendChild(this._wrapMessage('assistant', el, messageId));
    this.scrollToBottom();
  }

  _extractMessagePlainText(row, role) {
    if (!row) return '';
    if (role === 'user') {
      const ut = row.querySelector('.user-text');
      if (ut?.dataset.rawText) return ut.dataset.rawText.trim();
      if (!ut) return '';
      const clone = ut.cloneNode(true);
      clone.querySelectorAll('.mention-spoiler-body').forEach((el) => el.remove());
      return clone.textContent.trim();
    }
    const bubble = row.querySelector('.message-bubble');
    if (!bubble) return '';
    const clone = bubble.cloneNode(true);
    clone.querySelectorAll('img').forEach((img) => img.remove());
    return clone.innerText.trim();
  }

  _flashCopySuccess(btn) {
    if (!btn || btn.dataset.action !== 'copy') return;
    clearTimeout(btn._copyFlashTimer);
    const prev = {
      html: btn.innerHTML,
      title: btn.title,
      label: btn.getAttribute('aria-label'),
    };
    btn.classList.add('is-copied');
    btn.innerHTML = MSG_ICONS.check;
    btn.title = 'Скопировано!';
    btn.setAttribute('aria-label', 'Скопировано');
    btn._copyFlashTimer = setTimeout(() => {
      btn.classList.remove('is-copied');
      btn.innerHTML = prev.html;
      btn.title = prev.title;
      if (prev.label) btn.setAttribute('aria-label', prev.label);
      else btn.removeAttribute('aria-label');
      btn._copyFlashTimer = null;
    }, 1600);
  }

  async copyMessageText(messageId, role, copyBtn) {
    const row = this._findRow(messageId);
    const text = this._extractMessagePlainText(row, role);
    if (!text) {
      this.showError('Нет текста для копирования');
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } finally {
        ta.remove();
      }
    }
    this._flashCopySuccess(copyBtn);
    this.log?.info('msg', `Текст сообщения скопирован (${role})`);
  }

  async deleteMessage(messageId, role) {
    const row = this._findRow(messageId);
    if (!row) return;

    if (role === 'user') {
      const hasReplies = row.nextElementSibling !== null;
      const question = hasReplies
        ? 'Удалить сообщение и все ответы после него? Контекст беседы будет обрезан.'
        : 'Удалить это сообщение?';
      if (!confirm(question)) return;
    } else if (!confirm('Удалить ответ ассистента из чата и контекста?')) {
      return;
    }

    if (this.editingMessageId === messageId) {
      this.editingMessageId = null;
      this.editingRole = null;
      this.$.userInput.placeholder = this._inputPlaceholderDefault;
    }

    if (this.streaming) {
      this.socket?.cancel();
    }

    const cascade = role === 'user';
    this.log?.info('msg', `Удаление ${role} ${messageId} cascade=${cascade}`);
    try {
      await this.api(
        `/api/conversations/${this.currentConvId}/messages/${messageId}?cascade=${cascade}`,
        { method: 'DELETE' },
      );
      if (cascade) {
        this._removeFollowingRows(row, true);
      } else {
        row.remove();
      }
    } catch (err) {
      this.showError(err.message);
    }
  }

  editMessage(messageId, role) {
    if (this.streaming) {
      this.showError('Дождитесь окончания генерации');
      return;
    }
    if (!messageId) {
      this.showError('Сообщение ещё не сохранено. Дождитесь подтверждения отправки.');
      return;
    }
    const row = this._findRow(messageId);
    if (!row) return;

    const text = this._extractMessagePlainText(row, role);
    if (role === 'user') {
      this.$.userInput.placeholder = 'Enter — сохранить и перегенерировать ответ';
    } else {
      this.$.userInput.placeholder = 'Enter — сохранить изменения';
    }

    this.editingMessageId = messageId;
    this.editingRole = role;
    this.$.userInput.value = text.trim();
    this.autoResizeInput();
    this.$.userInput.focus();
  }

  async regenerateMessage(messageId) {
    const row = this._findRow(messageId);
    if (!row) return;
    if (row.classList.contains('user')) {
      await this._runRegenerate(messageId);
    } else {
      await this._runRegenerate(messageId, { fromAssistant: true });
    }
  }

  /**
   * Перегенерация ответа.
   * user: удаляет только ответы после сообщения, user остаётся.
   * assistant: удаляет этот ответ и всё после, затем ответ на предыдущий user.
   */
  async _runRegenerate(messageId, { fromAssistant = false } = {}) {
    if (this.streaming || !this.socket) return;
    const row = this._findRow(messageId);
    if (!row) return;

    if (fromAssistant) {
      this._removeFollowingRows(row, true);
    } else {
      this._removeFollowingRows(row, false);
    }

    this.log?.info('msg', `Перегенерация ${fromAssistant ? 'assistant' : 'user'} ${messageId}`);
    this._regenerating = true;
    this.startStreaming();
    try {
      this.socket.sendRegenerate(messageId, this.getWsIntegrationPayload());
    } catch (err) {
      this.showError(err.message);
      this.endStreaming();
      this._regenerating = false;
    }
  }

  _gridHasImageKey(grid, url) {
    const key = imageUrlKey(url);
    if (!key) return true;
    for (const img of grid.querySelectorAll('img')) {
      if (imageUrlKey(img.dataset.url || img.getAttribute('src')) === key) {
        return true;
      }
    }
    return false;
  }

  _setGridImages(grid, urls) {
    grid.innerHTML = '';
    const unique = [];
    const seen = new Set();
    for (const raw of urls || []) {
      const resolved = resolveMediaUrl(raw);
      const key = imageUrlKey(resolved);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      unique.push(resolved);
    }
    for (const resolved of unique) {
      grid.appendChild(this._createImage(resolved));
    }
    return unique.length;
  }

  _appendImagesToGrid(grid, urls) {
    let added = 0;
    for (const raw of urls || []) {
      const resolved = resolveMediaUrl(raw);
      if (!resolved || this._gridHasImageKey(grid, resolved)) continue;
      grid.appendChild(this._createImage(resolved));
      added += 1;
    }
    return added;
  }

  _createImage(url) {
    const resolved = resolveMediaUrl(url);
    const img = document.createElement('img');
    img.src = resolved;
    img.dataset.url = resolved;
    img.alt = 'Изображение';
    img.loading = 'lazy';
    img.addEventListener('click', () => this.openLightbox(resolved));
    img.addEventListener('load', () => this.scrollToBottom(), { once: true });
    return img;
  }

  _bindImageClicks(container) {
    container.querySelectorAll('img').forEach((img) => {
      img.addEventListener('click', (e) => {
        e.preventDefault();
        this.openLightbox(img.src);
      });
    });
  }

  async uploadFiles(fileList) {
    if (!this.currentConvId) {
      this.showError('Сначала выберите или создайте беседу');
      return;
    }
    const files = Array.from(fileList);
    const max = this.config.max_files_per_message || 10;
    if (this.pendingAttachments.length + files.length > max) {
      this.showError(`Максимум ${max} файлов за сообщение`);
      return;
    }

    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    fd.append('conversation_id', this.currentConvId);

    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Ошибка загрузки');
      }
      const data = await res.json();
      for (const att of data.attachments) {
        this.pendingAttachments.push(att);
        this.renderAttachmentChip(att);
      }
      this.$.attachmentStrip.classList.remove('hidden');
    } catch (err) {
      this.showError(err.message);
    }
    this.$.fileInput.value = '';
  }

  renderAttachmentChip(att) {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';
    chip.dataset.id = att.id;
    const preview = att.preview_url
      ? `<img src="${this.escapeAttr(att.preview_url)}" alt="">`
      : '<span class="chip-file-icon">📄</span>';
    chip.innerHTML = `${preview}<span class="chip-name">${this.escape(att.original_name)}</span>
      <button type="button" class="attachment-chip-remove" title="Убрать" aria-label="Убрать">×</button>`;
    chip.querySelector('.attachment-chip-remove').addEventListener('click', () => {
      this.pendingAttachments = this.pendingAttachments.filter((a) => a.id !== att.id);
      chip.remove();
      if (!this.pendingAttachments.length) {
        this.$.attachmentStrip.classList.add('hidden');
      }
    });
    this.$.attachmentStrip.appendChild(chip);
  }

  clearAttachments() {
    this.pendingAttachments = [];
    this.$.attachmentStrip.innerHTML = '';
    this.$.attachmentStrip.classList.add('hidden');
  }

  _restorePendingAttachmentsFromSession() {
    const raw = sessionStorage.getItem(PENDING_ATTACHMENTS_KEY);
    if (!raw) return;
    sessionStorage.removeItem(PENDING_ATTACHMENTS_KEY);
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (payload.conversation_id && payload.conversation_id !== this.currentConvId) return;
    const list = payload.attachments;
    if (!Array.isArray(list) || !list.length) return;
    for (const att of list) {
      if (!att?.id) continue;
      if (this.pendingAttachments.some((a) => a.id === att.id)) continue;
      this.pendingAttachments.push(att);
      this.renderAttachmentChip(att);
    }
    if (this.pendingAttachments.length) {
      this.$.attachmentStrip.classList.remove('hidden');
      this.$.userInput?.focus();
    }
  }

  _setSettingsChatTitle(title) {
    const el = this.$.settingsChatTitle;
    if (!el) return;
    if (!this.currentConvId) {
      el.disabled = true;
      el.value = '';
      el.placeholder = 'Выберите или создайте беседу';
      this._updateExportButton();
      return;
    }
    el.disabled = false;
    el.value = title ?? this.currentConv?.title ?? '';
    el.placeholder = 'Название беседы';
    this._updateExportButton();
  }

  _settingsChatTitleDraft() {
    const raw = this.$.settingsChatTitle?.value?.trim() ?? '';
    return raw || 'Новая беседа';
  }

  async saveSettings() {
    if (!this.$.settingsSaveBtn) return;
    const convPresetId = this.$.convPresetSelect?.value;

    const btn = this.$.settingsSaveBtn;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.classList.remove('is-success');
    btn.classList.add('is-saving');
    btn.setAttribute('aria-label', 'Сохранение…');
    this._hideSettingsSaveStatus();

    try {
      if (this.currentConvId) {
        const patch = {};
        const nextTitle = this._settingsChatTitleDraft();
        if (this.currentConv && this.currentConv.title !== nextTitle) {
          patch.title = nextTitle;
        }
        if (convPresetId && this.currentConv?.preset_id !== convPresetId) {
          patch.preset_id = convPresetId;
        }
        if (Object.keys(patch).length > 0) {
          this.currentConv = await this.api(`/api/conversations/${this.currentConvId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
          });
          this._setSettingsChatTitle(this.currentConv.title);
          const conv = this.conversations.find((c) => c.id === this.currentConvId);
          if (conv) conv.title = this.currentConv.title;
          this.renderConvList();
          if (patch.preset_id) {
            if (this.$.chatPresetSelect) this.$.chatPresetSelect.value = patch.preset_id;
          }
        }
      }

      await this.savePresetPromptIfDirty({ silent: true });

      this.applyFontSize();
      this._saveModelOverride();
      this._saveIntegrationUrls();
      await this.loadLlmModelInfo();
      if (this.$.useServerModel) {
        localStorage.setItem(
          'webchat_use_server_model',
          this.$.useServerModel.checked ? 'true' : 'false',
        );
      }

      btn.classList.remove('is-saving');
      btn.classList.add('is-success');
      btn.setAttribute('aria-label', 'Сохранено');
      this.log?.info('settings', 'Настройки сохранены');
    } catch (err) {
      btn.classList.remove('is-saving', 'is-success');
      btn.setAttribute('aria-label', 'Сохранить настройки');
      this._showSettingsSaveStatus('error', err.message || 'Не удалось сохранить');
    } finally {
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
      btn.classList.remove('is-saving');
      clearTimeout(this._settingsSaveBtnTimer);
      this._settingsSaveBtnTimer = setTimeout(() => {
        if (!this.$.settingsSaveBtn) return;
        this.$.settingsSaveBtn.classList.remove('is-success');
        this.$.settingsSaveBtn.setAttribute('aria-label', 'Сохранить настройки');
      }, 2200);
    }
  }

  _hideSettingsSaveStatus() {
    if (!this.$.settingsSaveStatus) return;
    clearTimeout(this._settingsSaveStatusTimer);
    this.$.settingsSaveStatus.textContent = '';
    this.$.settingsSaveStatus.className = 'settings-save-status';
  }

  _showSettingsSaveStatus(kind, message) {
    if (!this.$.settingsSaveStatus) return;
    clearTimeout(this._settingsSaveStatusTimer);
    this.$.settingsSaveStatus.textContent = message;
    this.$.settingsSaveStatus.className = `settings-save-status is-${kind} is-visible`;
    this._settingsSaveStatusTimer = setTimeout(() => {
      this._hideSettingsSaveStatus();
    }, 4000);
  }

  showError(msg, autoHideMs = 8000) {
    this.log?.error('ui', msg);
    clearTimeout(this._errorTimer);
    this.$.errorBannerText.textContent = msg;
    this.$.errorBanner.classList.remove('hidden');
    if (autoHideMs > 0) {
      this._errorTimer = setTimeout(() => this.hideError(), autoHideMs);
    }
  }

  hideError() {
    this.$.errorBanner.classList.add('hidden');
    clearTimeout(this._errorTimer);
  }

  showProgress(text) {
    if (!this.streamEl) return;
    const status = this.streamEl.querySelector('.message-status');
    const label = this.streamEl.querySelector('.message-status-text');
    if (!status || !label) return;
    label.textContent = text;
    status.classList.remove('hidden');
    this.streamEl.classList.add('waiting', 'is-busy');
  }

  hideProgress() {
    if (!this.streamEl) return;
    const status = this.streamEl.querySelector('.message-status');
    status?.classList.add('hidden');
    this.streamEl.classList.remove('is-busy');
    const hasBody = this.streamEl.classList.contains('has-content')
      || this.streamEl.classList.contains('has-images');
    if (hasBody) {
      this.streamEl.classList.remove('waiting');
    }
  }

  _distanceFromBottom(el = this.$.chatHistory) {
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  _onChatScroll() {
    const dist = this._distanceFromBottom();
    if (dist <= SCROLL_STICKY_PX) {
      this._scrollStuckToBottom = true;
    } else {
      this._scrollStuckToBottom = false;
    }
    this._updateScrollBtn();
  }

  scrollToBottom(force = false) {
    const el = this.$.chatHistory;
    if (force) {
      this._scrollStuckToBottom = true;
    }
    if (force || this._scrollStuckToBottom) {
      el.scrollTop = el.scrollHeight;
    }
    this._updateScrollBtn();
  }

  _updateScrollBtn() {
    const el = this.$.chatHistory;
    const dist = this._distanceFromBottom(el);
    const show = dist > SCROLL_STICKY_PX;
    this.$.scrollBtn.classList.toggle('visible', show);
    if (show) {
      this.$.scrollBtn.title = this._scrollStuckToBottom
        ? 'Вниз'
        : 'Вниз (прилипнуть к новым сообщениям)';
    }
  }

  _collectGalleryUrls() {
    const urls = [];
    const seen = new Set();
    const add = (raw) => {
      const resolved = resolveMediaUrl(raw);
      if (!resolved || seen.has(resolved)) return;
      seen.add(resolved);
      urls.push(resolved);
    };
    this.$.chatMessages.querySelectorAll('.message-images img').forEach((img) => {
      add(img.dataset.url || img.getAttribute('src'));
    });
    this.$.chatMessages.querySelectorAll('.message-bubble img, .md-inline-img').forEach((img) => {
      add(img.getAttribute('src'));
    });
    return urls;
  }

  _lightboxCurrentUrl() {
    return this._lightboxUrls[this._lightboxIndex] || this.$.lightboxImg?.src || '';
  }

  _filenameFromLightboxUrl(url) {
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

  async downloadLightboxImage() {
    const url = resolveMediaUrl(this._lightboxCurrentUrl());
    if (!url) return;
    const btn = this.$.lightboxSave;
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('Не удалось загрузить файл');
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = this._filenameFromLightboxUrl(url);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      this.showError(err.message || 'Не удалось скачать изображение');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async attachLightboxImageToComposer() {
    const url = resolveMediaUrl(this._lightboxCurrentUrl());
    if (!url) return;
    if (!this.currentConvId) {
      this.showError('Сначала выберите или создайте беседу');
      return;
    }
    const key = imageUrlKey(url);
    if (this.pendingAttachments.some((a) => imageUrlKey(resolveMediaUrl(a.preview_url)) === key)) {
      this.showError('Это изображение уже прикреплено', 3000);
      return;
    }
    const btn = this.$.lightboxAttachCurrent;
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('Не удалось загрузить изображение');
      const blob = await res.blob();
      const mime = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
      const file = new File([blob], this._filenameFromLightboxUrl(url), { type: mime });
      await this.uploadFiles([file]);
      this.closeLightbox();
      this.$.userInput?.focus();
    } catch (err) {
      this.showError(err.message || 'Ошибка прикрепления');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  openLightbox(url) {
    const resolved = resolveMediaUrl(url);
    this._lightboxUrls = this._collectGalleryUrls();
    if (!this._lightboxUrls.length) {
      this._lightboxUrls = [resolved];
    }
    let index = this._lightboxUrls.indexOf(resolved);
    if (index < 0) {
      this._lightboxUrls.push(resolved);
      index = this._lightboxUrls.length - 1;
    }
    this._showLightboxAt(index);
    document.body.style.overflow = 'hidden';
  }

  _showLightboxAt(index) {
    if (!this._lightboxUrls.length) return;
    this._lightboxIndex = Math.max(0, Math.min(index, this._lightboxUrls.length - 1));
    const url = this._lightboxUrls[this._lightboxIndex];
    this.$.lightboxImg.src = url;
    this.$.lightboxImg.alt = `Изображение ${this._lightboxIndex + 1} из ${this._lightboxUrls.length}`;
    this.$.lightbox.classList.remove('hidden');
    this._updateLightboxNav();
    this._updateLightboxActions();
  }

  _updateLightboxActions() {
    if (this.$.lightboxAttachCurrent) {
      this.$.lightboxAttachCurrent.disabled = !this.currentConvId;
    }
  }

  _updateLightboxNav() {
    const n = this._lightboxUrls.length;
    const i = this._lightboxIndex;
    this.$.lightboxPrev.disabled = i <= 0;
    this.$.lightboxNext.disabled = i >= n - 1;
    if (n > 1) {
      this.$.lightboxCounter.textContent = `${i + 1} / ${n}`;
      this.$.lightboxCounter.classList.remove('hidden');
    } else {
      this.$.lightboxCounter.classList.add('hidden');
    }
  }

  _lightboxStep(delta) {
    if (this.$.lightbox.classList.contains('hidden')) return;
    const next = this._lightboxIndex + delta;
    if (next < 0 || next >= this._lightboxUrls.length) return;
    this._showLightboxAt(next);
  }

  _onLightboxTouchStart(e) {
    if (e.touches.length !== 1) return;
    this._lightboxTouchStart = {
      x: e.touches[0].clientX,
      y: e.touches[0].clientY,
    };
  }

  _onLightboxTouchEnd(e) {
    if (!this._lightboxTouchStart || e.changedTouches.length !== 1) return;
    const dx = e.changedTouches[0].clientX - this._lightboxTouchStart.x;
    const dy = e.changedTouches[0].clientY - this._lightboxTouchStart.y;
    this._lightboxTouchStart = null;
    if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy)) return;
    this._lightboxStep(dx < 0 ? 1 : -1);
  }

  closeLightbox() {
    this.$.lightbox.classList.add('hidden');
    this.$.lightboxImg.src = '';
    this._lightboxUrls = [];
    this._lightboxIndex = 0;
    this._lightboxTouchStart = null;
    if (this.$.lightboxAttachCurrent) this.$.lightboxAttachCurrent.disabled = false;
    if (!this.$.convSidebar.classList.contains('open')) {
      document.body.style.overflow = '';
    }
  }

  autoResizeInput() {
    const ta = this.$.userInput;
    if (!ta) return;

    const style = getComputedStyle(ta);
    const lineHeight = parseFloat(style.lineHeight) || 20;
    const padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const borderY = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const maxRows = 7;
    const minH = Math.max(
      parseFloat(style.minHeight) || 0,
      lineHeight + padY + borderY,
    );
    const maxH = lineHeight * maxRows + padY + borderY;

    ta.style.height = '0px';
    const contentH = ta.scrollHeight;
    const next = Math.min(Math.max(contentH, minH), maxH);
    ta.style.height = `${next}px`;
    ta.classList.toggle('chat-input--scrollable', contentH > maxH + 1);
    if (contentH > maxH) {
      ta.scrollTop = ta.scrollHeight;
    }
  }

  _loadTheme() {
    const dark = localStorage.getItem('webchat_theme') === 'dark'
      || (!localStorage.getItem('webchat_theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.classList.toggle('dark-theme', dark);
  }

  _updateThemeToggleLabel() {
    if (!this.$.themeToggleLabel) return;
    const dark = document.body.classList.contains('dark-theme');
    this.$.themeToggleLabel.textContent = dark ? 'Тёмная тема' : 'Светлая тема';
  }

  _loadModelSettings() {
    if (!this.$.useServerModel) return;
    const stored = localStorage.getItem('webchat_use_server_model');
    if (stored !== null) {
      this.$.useServerModel.checked = stored !== 'false';
    }
    this.$.useServerModel.addEventListener('change', () => {
      localStorage.setItem(
        'webchat_use_server_model',
        this.$.useServerModel.checked ? 'true' : 'false',
      );
      this._syncModelInputState();
    });
  }

  _normalizeServiceUrl(raw, { stripV1 = false } = {}) {
    const text = (raw || '').trim();
    if (!text) return '';
    let url = text.replace(/\/+$/, '');
    if (stripV1) {
      url = url.replace(/\/v1$/i, '');
    }
    return url;
  }

  _loadIntegrationUrlFields() {
    const llmDefault = this.config?.llm_base_url || '';
    const sdDefault = this.config?.sd_webui_url || '';
    if (this.$.llmBaseUrlInput) {
      this.$.llmBaseUrlInput.value = localStorage.getItem('webchat_llm_base_url')
        || llmDefault;
    }
    if (this.$.sdWebuiUrlInput) {
      this.$.sdWebuiUrlInput.value = localStorage.getItem('webchat_sd_webui_url')
        || sdDefault;
    }
  }

  _saveIntegrationUrls() {
    const llm = this._normalizeServiceUrl(this.$.llmBaseUrlInput?.value);
    const sd = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
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
  }

  getWsIntegrationPayload() {
    const payload = {};
    const llmUrl = this._normalizeServiceUrl(this.$.llmBaseUrlInput?.value);
    const sdUrl = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
    if (llmUrl) payload.llm_base_url = llmUrl;
    if (sdUrl) payload.sd_webui_url = sdUrl;
    const model = this.getActiveLlmModel();
    if (model) payload.model = model;
    return payload;
  }

  async loadLlmModelInfo() {
    try {
      const base = this._normalizeServiceUrl(this.$.llmBaseUrlInput?.value);
      const qs = base ? `?llm_base_url=${encodeURIComponent(base)}` : '';
      const info = await this.api(`/api/config/llm-model${qs}`);
      this._serverLlmModel = info.resolved || '';
      this._serverLlmSource = info.source || 'auto';
      this._syncModelInputState();
    } catch {
      if (this.$.llmModelInput) {
        this.$.llmModelInput.placeholder = 'Недоступно';
      }
    }
  }

  _syncModelInputState() {
    if (!this.$.llmModelInput || !this.$.useServerModel) return;
    const useServer = this.$.useServerModel.checked;
    this.$.llmModelInput.readOnly = useServer;
    if (useServer) {
      this.$.llmModelInput.value = this._serverLlmModel;
      this.$.llmModelInput.title = this._serverLlmSource === 'config'
        ? 'Из конфигурации сервера'
        : 'Автовыбор с указанного API';
    } else {
      const saved = localStorage.getItem('webchat_llm_model_override') || '';
      this.$.llmModelInput.value = saved;
      this.$.llmModelInput.title = 'Переопределение для запросов из браузера';
    }
  }

  _saveModelOverride() {
    if (!this.$.llmModelInput || this.$.useServerModel?.checked) return;
    localStorage.setItem('webchat_llm_model_override', this.$.llmModelInput.value.trim());
  }

  getActiveLlmModel() {
    if (!this.$.useServerModel || this.$.useServerModel.checked) return undefined;
    const v = (this.$.llmModelInput?.value || '').trim();
    return v || undefined;
  }

  _loadFontSize() {
    const saved = parseInt(localStorage.getItem('webchat_font_size') || '', 10);
    if (this.$.fontSizeInput && !Number.isNaN(saved)) {
      this.$.fontSizeInput.value = String(saved);
    }
    this.applyFontSize();
  }

  applyFontSize() {
    if (!this.$.fontSizeInput) return;
    const n = parseInt(this.$.fontSizeInput.value, 10) || 14;
    const clamped = Math.max(8, Math.min(20, n));
    this.$.fontSizeInput.value = String(clamped);
    document.documentElement.style.setProperty('--font-size', `${clamped}px`);
    localStorage.setItem('webchat_font_size', String(clamped));
    this.autoResizeInput();
  }

  changeFontSize(delta) {
    if (!this.$.fontSizeInput) return;
    const current = parseInt(this.$.fontSizeInput.value, 10) || 14;
    this.$.fontSizeInput.value = String(current + delta);
    this.applyFontSize();
  }

  toggleTheme() {
    const dark = !document.body.classList.contains('dark-theme');
    document.body.classList.toggle('dark-theme', dark);
    localStorage.setItem('webchat_theme', dark ? 'dark' : 'light');
    this._updateThemeToggleLabel();
  }

  async openLogsPanel() {
    await this._fetchServerLogs();
    this._renderLogsView();
    this._stopLogsLiveUpdate();
    this._logsUnsub = this.log?.subscribe(() => {
      if (this._isLogsPanelOpen()) this._renderLogsView();
    }) || null;
    this.showPanel('logs');
    requestAnimationFrame(() => {
      if (this.$.logsOutput) {
        this.$.logsOutput.scrollTop = this.$.logsOutput.scrollHeight;
      }
    });
  }

  closeLogsPanel() {
    this.showPanel('main');
  }

  _stopLogsLiveUpdate() {
    if (this._logsUnsub) {
      this._logsUnsub();
      this._logsUnsub = null;
    }
  }

  async _fetchServerLogs() {
    try {
      const res = await fetch('/api/logs?limit=300');
      if (!res.ok) {
        this._serverLogLines = [];
        return;
      }
      const data = await res.json();
      this._serverLogLines = data.lines || [];
    } catch (err) {
      this._serverLogLines = [];
      this.log?.warn('app', 'Не удалось загрузить серверный журнал', err.message);
    }
  }

  _renderLogsView() {
    const parts = [];
    if (this._serverLogLines.length) {
      parts.push('=== Сервер ===');
      parts.push(...this._serverLogLines);
      parts.push('');
    }
    parts.push('=== Клиент (сессия) ===');
    parts.push(this.log?.getText() || '');
    const text = parts.join('\n');
    this.$.logsOutput.value = text;
    const lineCount = text.split('\n').filter((l) => l.length > 0).length;
    this.$.logsCount.textContent = `${lineCount} строк`;
  }

  async copyAllLogs() {
    const text = this.$.logsOutput.value;
    try {
      await navigator.clipboard.writeText(text);
      this.log?.info('app', 'Журнал скопирован в буфер обмена');
      if (this._isLogsPanelOpen()) this._renderLogsView();
    } catch {
      this.$.logsOutput.focus();
      this.$.logsOutput.select();
      document.execCommand('copy');
    }
  }

  async clearAllLogs() {
    if (!confirm('Очистить журнал клиента и сервера?')) return;
    this.log?.clear();
    this._serverLogLines = [];
    try {
      await fetch('/api/logs', { method: 'DELETE' });
    } catch {
      /* ignore */
    }
    this._renderLogsView();
    this.log?.info('app', 'Журнал очищен');
  }

  escape(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  escapeAttr(s) {
    return String(s).replace(/"/g, '&quot;');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.chatApp = new ChatApp();
});
