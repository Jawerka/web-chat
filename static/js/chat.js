/**
 * web-chat UI — REST + WebSocket
 */
/* global formatMarkdown, escapeHtml, escapeAttr, formatApiErrorDetail */

/** Порог для кнопки «вниз» и ручного «прилипания». */
const SCROLL_STICKY_PX = 72;
/** Автоскролл при стриминге только если пользователь почти у низа. */
const SCROLL_FOLLOW_PX = 28;

/** Задержка перед оверлеем при смене беседы (быстрые переключения без мигания). */
const CONV_SWITCH_OVERLAY_DELAY_MS = 140;

/** Состояния UI чата (доминирующее для индикаторов). */
const CHAT_UI_STATE = {
  CONNECTING: 'connecting',
  READY: 'ready',
  STREAMING: 'streaming',
  TOOL_RUNNING: 'tool_running',
  RECONNECTING: 'reconnecting',
  OFFLINE: 'offline',
  ERROR: 'error',
};

const TOOL_ACTIVITY_STAGES = new Set([
  'sd_render',
  'sd_upscale',
  'doc_read',
  'gallery',
  'llm_tools',
  'save_media',
]);

const MESSAGE_SKELETON_ROWS = 4;

/** sessionStorage: selected | full | semantic (Ф1/Ф2) */
const MACRO_CONTEXT_MODE_KEY = 'webchat_macro_context_mode';
const MACRO_CONTEXT_FULL_LEGACY = 'webchat_macro_context_full';
const DOCUMENT_RAG_KEY = 'webchat_document_rag_enabled';

class ChatApp {
  constructor() {
    this.conversations = [];
    this.trashConversations = [];
    this._trashOpen = false;
    this._pendingTrashDeleteId = null;
    this._pendingTrashDeleteBtn = null;
    this._pendingEmptyTrash = false;
    this.presets = [];
    this.currentConvId = null;
    this.currentConv = null;
    this.pendingAttachments = [];
    this.socket = null;
    this.streaming = false;
    this.streamText = '';
    this.streamReasoningText = '';
    this.streamEl = null;
    this.streamImagesEl = null;
    this.config = { max_files_per_message: 10 };
    this._errorTimer = null;
    this.editingMessageId = null;
    this.editingRole = null;
    this._editComposerBackup = null;
    this._regenerating = false;
    this._inputPlaceholderDefault = 'Сообщение';
    this._lightboxReturnFocus = null;
    this._serverLogLines = [];
    this._logsUnsub = null;
    this._pendingDeleteConvId = null;
    this._pendingDeleteBtn = null;
    this._pendingDeleteMessageId = null;
    this._pendingDeleteMessageBtn = null;
    this._pendingDeleteMessageRole = null;
    this._deletingConvIds = new Set();
    this._scrollStuckToBottom = true;
    this._lightboxUrls = [];
    this._lightboxIndex = 0;
    this._lightboxTouchStart = null;
    this._generationSyncTimer = null;
    this._globalSyncTimer = null;
    this._generationResumeActive = false;
    this._generationWatchRunning = false;
    this._generationHadImages = false;
    this._generationNotifyPending = false;
    this._wsOfflineBannerShown = false;
    this._wsReconnecting = false;
    this._uiState = CHAT_UI_STATE.READY;
    this._uiActivityStage = null;
    this._messagesLoading = false;
    this._conversationsFingerprint = '';
    this._messagesFingerprint = '';
    this._scrollRaf = null;
    this._globalSyncIntervalMs = 3500;
    this._sidebarSwipe = null;
    this._serverLlmModel = '';
    this._serverLlmSource = 'auto';
    this._sdModels = [];
    this._sdSelectedServer = '';
    this._settingsSaveStatusTimer = null;
    this._settingsSaveBtnTimer = null;
    this._presetPromptSaveBtnTimer = null;
    this._presetDraftDebounceTimer = null;
    this._editingPresetId = null;
    this._searchDebounceTimer = null;
    this._inlineTitleConvId = null;
    this._composerDraftDebounceTimer = null;
    this._scrollPositionSaveTimer = null;
    this._ragPreviewTimer = null;
    this._ragPreviewSeq = 0;
    this._suppressScrollPositionSave = false;
    this._convSwitchOverlayTimer = null;
    this._fileDragDepth = 0;
    this._uploadInProgress = false;
    this._uploadToastTimer = null;
    this._pendingImageDeleteKey = null;
    this._pendingImageDeleteBtn = null;
    this._favoriteStateCache = new Map();
    this.currentUser = null;
    this.log = window.appLog;
    this.promptMacros = new PromptMacrosUI(this);

    this.$ = {
      app: document.getElementById('app'),
      backdrop: document.getElementById('sidebar-backdrop'),
      convSearch: document.getElementById('conv-search'),
      convSearchToggle: document.getElementById('conv-search-toggle'),
      convSearchStack: document.getElementById('conv-search-stack'),
      convSearchPanel: document.getElementById('conv-search-panel'),
      convSearchClose: document.getElementById('conv-search-close'),
      convSearchResults: document.getElementById('conv-search-results'),
      convList: document.getElementById('conv-list'),
      convEmpty: document.getElementById('conv-empty'),
      convTrashTabBtn: document.getElementById('conv-trash-tab-btn'),
      convTrashPanel: document.getElementById('conv-trash-panel'),
      convTrashList: document.getElementById('conv-trash-list'),
      convTrashCount: document.getElementById('conv-trash-count'),
      convTrashEmpty: document.getElementById('conv-trash-empty'),
      convTrashHint: document.getElementById('conv-trash-hint'),
      convTrashEmptyAll: document.getElementById('conv-trash-empty-all'),
      convSidebar: document.getElementById('conv-sidebar'),
      convSidebarSheet: document.querySelector('.conv-sidebar-sheet'),
      chatPanel: document.querySelector('.chat-panel'),
      floatingSettings: document.getElementById('floating-settings'),
      settingsPanel: document.getElementById('settings-panel'),
      logsPanel: document.getElementById('logs-panel'),
      macroInsertBtn: document.getElementById('macro-insert-btn'),
      macroContextFullBtn: document.getElementById('macro-context-full-btn'),
      documentRagBtn: document.getElementById('document-rag-btn'),
      composerMoreBtn: document.getElementById('composer-more-btn'),
      composerToolsMenu: document.getElementById('composer-tools-menu'),
      preloadModelsBtn: document.getElementById('preload-models-btn'),
      documentRagPreview: document.getElementById('document-rag-preview'),
      macroInsertMenuBtn: document.getElementById('macro-insert-menu-btn'),
      settingsChatTitle: document.getElementById('settings-chat-title'),
      generateConversationTitleBtn: document.getElementById('generate-conversation-title-btn'),
      exportConversationBtn: document.getElementById('export-conversation-btn'),
      convPresetSelect: document.getElementById('conv-preset-select'),
      chatPresetToolbar: document.getElementById('chat-preset-toolbar'),
      chatPresetSelect: document.getElementById('chat-preset-select'),
      img2imgGenPresetToggle: document.getElementById('img2img-gen-preset-toggle'),
      img2imgGenPresetPanel: document.getElementById('img2img-gen-preset-panel'),
      img2imgGenPresetEnabled: document.getElementById('img2img-gen-preset-enabled'),
      img2imgGenPresetPreview: document.getElementById('img2img-gen-preset-preview'),
      img2imgGenPresetFields: document.getElementById('img2img-gen-preset-fields'),
      img2imgDenoiseMin: document.getElementById('img2img-denoise-min'),
      img2imgDenoiseMax: document.getElementById('img2img-denoise-max'),
      img2imgCfgMin: document.getElementById('img2img-cfg-min'),
      img2imgCfgMax: document.getElementById('img2img-cfg-max'),
      img2imgCount: document.getElementById('img2img-count'),
      presetSelect: document.getElementById('preset-select'),
      presetSystemPrompt: document.getElementById('preset-system-prompt'),
      presetPromptSaveBtn: document.getElementById('preset-prompt-save-btn'),
      presetSetDefaultBtn: document.getElementById('preset-set-default-btn'),
      settingsSaveBtn: document.getElementById('settings-save-btn'),
      settingsSaveStatus: document.getElementById('settings-save-status'),
      settingsBtn: document.getElementById('settings-btn'),
      connStatus: document.getElementById('conn-status'),
      connStatusLabel: document.getElementById('conn-status-label'),
      placeholder: document.getElementById('placeholder'),
      chatHistory: document.getElementById('chat-history'),
      chatMessages: document.getElementById('chat-messages'),
      chatBody: document.getElementById('chat-body'),
      chatDropOverlay: document.getElementById('chat-drop-overlay'),
      chatDropOverlayTitle: document.getElementById('chat-drop-overlay-title'),
      uploadToast: document.getElementById('upload-toast'),
      chatComposer: document.getElementById('chat-composer'),
      userInput: document.getElementById('user-input'),
      sendBtn: document.getElementById('send-btn'),
      cancelBtn: document.getElementById('cancel-btn'),
      fileInput: document.getElementById('file-input'),
      attachmentStrip: document.getElementById('attachment-strip'),
      errorBanner: document.getElementById('error-banner'),
      errorBannerText: document.getElementById('error-banner-text'),
      errorBannerRetry: document.getElementById('error-banner-retry'),
      scrollBtn: document.getElementById('scroll-to-bottom-btn'),
      loadingOverlay: document.getElementById('loading-overlay'),
      lightbox: document.getElementById('lightbox'),
      lightboxStage: document.getElementById('lightbox-stage'),
      lightboxImg: document.getElementById('lightbox-img'),
      lightboxLoader: document.getElementById('lightbox-loader'),
      lightboxPrev: document.getElementById('lightbox-prev'),
      lightboxNext: document.getElementById('lightbox-next'),
      lightboxCounter: document.getElementById('lightbox-counter'),
      lightboxSave: document.getElementById('lightbox-save'),
      lightboxFavorite: document.getElementById('lightbox-favorite'),
      lightboxPromote: document.getElementById('lightbox-promote'),
      lightboxAttachCurrent: document.getElementById('lightbox-attach-current'),
      themeToggle: document.getElementById('theme-toggle'),
      themeToggleLabel: document.getElementById('theme-toggle-label'),
      llmBaseUrlInput: document.getElementById('llm-base-url-input'),
      llmModelInput: document.getElementById('llm-model-input'),
      sdWebuiUrlInput: document.getElementById('sd-webui-url-input'),
      sdModelSelect: document.getElementById('sd-model-select'),
      sdModelRefreshBtn: document.getElementById('sd-model-refresh-btn'),
      useServerModel: document.getElementById('use-server-model'),
      fontSizeInput: document.getElementById('font-size'),
      fontSizeDecrease: document.getElementById('font-size-decrease'),
      fontSizeIncrease: document.getElementById('font-size-increase'),
      logsOutput: document.getElementById('logs-output'),
      logsCount: document.getElementById('logs-count'),
      accountSection: document.getElementById('settings-account-section'),
      adminSection: document.getElementById('settings-admin-section'),
      accountLogin: document.getElementById('settings-account-login'),
      accountRole: document.getElementById('settings-account-role'),
      authLogoutBtn: document.getElementById('auth-logout-btn'),
      usersList: document.getElementById('settings-users-list'),
      createUserForm: document.getElementById('settings-create-user-form'),
      createUserError: document.getElementById('settings-create-user-error'),
      changePasswordForm: document.getElementById('settings-change-password-form'),
      changePasswordError: document.getElementById('settings-change-password-error'),
    };

    this.log?.info('app', 'Интерфейс загружен');
    window.addEventListener('error', (ev) => {
      this.log?.error('window', ev.message || 'uncaught error', {
        filename: ev.filename,
        lineno: ev.lineno,
        colno: ev.colno,
      });
    });
    window.addEventListener('unhandledrejection', (ev) => {
      this.log?.error('promise', 'unhandled rejection', ev.reason);
    });
    this._bindEvents();
    if (typeof WebChatImg2imgPreset !== 'undefined') {
      WebChatImg2imgPreset.init(this);
    }
    WebChatAppearance.loadTheme();
    WebChatAppearance.updateThemeToggleLabel(this);
    WebChatAppearance.loadFontSize(this);
    this._loadModelSettings();
    this.showPanel('main');
    this.init();
  }

  async init() {
    try {
      const cfg = await this.api('/api/config');
      this.config = { ...this.config, ...cfg };
      WebChatDateTime.applyServerDefault(cfg.display_timezone);
      this._loadIntegrationUrlFields();
      this._updateTrustedInternalHint();
      if (this.config.auth_enabled) {
        const llm = this._normalizeServiceUrl(this.$.llmBaseUrlInput?.value);
        const sd = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
        if (llm || sd) this._syncTrustedInternalHosts(llm, sd);
      }
    } catch { /* optional */ }
    if (this.config.rag_enabled) {
      this.$.documentRagBtn?.classList.remove('hidden');
      this._initDocumentRagToggle();
    }
    WebChatSettings.loadWdTaggerSettings(this);
    if (this.config.auth_enabled) {
      try {
        this.currentUser = await this.api('/api/auth/me');
        WebChatAuth.initUi(this);
      } catch (err) {
        const msg = err?.message || '';
        if (msg.includes('Требуется вход') || msg.includes('401')) {
          const next = encodeURIComponent(window.location.pathname + window.location.search);
          window.location.replace(`/login?next=${next}`);
          return;
        }
      }
    }
    this.loadLlmModelInfo().catch(() => {});
    this.loadSdModelInfo().catch(() => {});

    await Promise.all([
      this.loadPresets(),
      this.loadConversations(),
      this.loadTrash(),
      this.promptMacros.load(),
    ]);
    if (this.config?.trash_retention_days && this.$.convTrashHint) {
      const days = this.config.trash_retention_days;
      this.$.convTrashHint.textContent = `Удалённые беседы хранятся ${days} ${
        days === 1 ? 'день' : days < 5 ? 'дня' : 'дней'
      }, затем удаляются навсегда`;
    }
    this.promptMacros.bindInputAutocomplete(this.$.userInput);
    const urlConv = new URLSearchParams(window.location.search).get('conv');
    const savedConv = urlConv || localStorage.getItem('webchat_conv_id');
    if (savedConv && this.conversations.some((c) => c.id === savedConv)) {
      await this.selectConversation(savedConv);
      if (urlConv) {
        const clean = new URL(window.location.href);
        clean.searchParams.delete('conv');
        const nextUrl = clean.pathname + (clean.search || '') + (clean.hash || '');
        window.history.replaceState({}, '', nextUrl);
      }
    }
    this._startGlobalSync();
  }

  _bindEvents() {
    WebChatConversations.bindSidebarEvents(this);

    document.getElementById('menu-btn').addEventListener('click', () => this.openSidebar());
    this.$.backdrop.addEventListener('click', () => this.closeSidebar());
    this._bindSidebarSwipeGestures();

    this.$.settingsBtn?.addEventListener('click', () => this.showPanel('settings'));
    WebChatAuth.bindEvents(this);
    this.$.macroInsertBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.promptMacros.isPickerOpen()) {
        this.promptMacros.closePicker();
      } else {
        this.promptMacros.openPicker();
      }
    });
    this.$.macroInsertMenuBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (this.promptMacros.isPickerOpen()) {
        this.promptMacros.closePicker();
      } else {
        this.promptMacros.openPicker();
      }
    });
    this._initMacroContextToggle();
    WebChatComposer.bindEvents(this);
    document.addEventListener('click', (e) => {
      const pop = document.getElementById('macro-picker-popover');
      if (!pop || pop.classList.contains('hidden')) return;
      if (
        pop.contains(e.target)
        || this.$.macroInsertBtn?.contains(e.target)
        || this.$.macroInsertMenuBtn?.contains(e.target)
      ) {
        return;
      }
      this.promptMacros.closePicker();
    });
    document.getElementById('settings-close')?.addEventListener('click', () => this.showPanel('main'));
    document.getElementById('logs-close')?.addEventListener('click', () => this.closeLogsPanel());
    this.$.themeToggle?.addEventListener('click', () => WebChatAppearance.toggleTheme(this));
    this.$.llmModelInput?.addEventListener('change', () => this._saveModelOverride());
    this.$.sdModelRefreshBtn?.addEventListener('click', () => {
      void this.loadSdModelInfo();
    });
    this.$.sdModelSelect?.addEventListener('change', () => {
      void this.applySdModelSelection({ showStatus: true });
    });
    this.$.sdWebuiUrlInput?.addEventListener('change', () => {
      void this.loadSdModelInfo();
    });
    this.$.fontSizeDecrease?.addEventListener('click', () => WebChatAppearance.changeFontSize(this, -1));
    this.$.fontSizeIncrease?.addEventListener('click', () => WebChatAppearance.changeFontSize(this, 1));
    this.$.fontSizeInput?.addEventListener('change', () => WebChatAppearance.applyFontSize(this));
    document.getElementById('logs-copy-all')?.addEventListener('click', () => this.copyAllLogs());
    document.getElementById('logs-clear-all')?.addEventListener('click', () => this.clearAllLogs());
    document.getElementById('error-banner-close').addEventListener('click', () => this.hideError());
    this.$.errorBannerRetry?.addEventListener('click', () => this._retrySocketConnection());
    this.$.cancelBtn?.addEventListener('click', () => this.cancelGeneration());
    this.$.preloadModelsBtn?.addEventListener('click', () => {
      void WebChatPreloadModels.run(this);
    });
    WebChatPresets.bindPresetEvents(this);
    window.addEventListener('beforeunload', (e) => {
      if (this._hasUnsyncedPresetDrafts()) {
        this._flushPresetDraftsToStorage();
        e.preventDefault();
        e.returnValue = '';
      }
    });
    this.$.settingsSaveBtn?.addEventListener('click', () => this.saveSettings());
    this.$.generateConversationTitleBtn?.addEventListener('click', () => {
      void WebChatSettings.generateConversationTitle(this);
    });
    this.$.exportConversationBtn?.addEventListener('click', () => this.exportCurrentConversation());
    this.$.settingsChatTitle?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        this.saveSettings();
      }
    });
    WebChatScroll.chatHistoryScrollEl(this)
      ?.addEventListener('scroll', () => this._onChatScroll());
    this.$.scrollBtn.addEventListener('click', () => this.scrollToBottom(true));
    this._bindMessageImageActions();

    let convTooltipResizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(convTooltipResizeTimer);
      convTooltipResizeTimer = setTimeout(() => this._updateConvTitleTooltips(), 150);
    });

    WebChatLightbox.bindEvents(this);

    document.addEventListener('keydown', (e) => {
      if (WebChatLightbox.onDocumentKeydown(this, e)) return;
      if (e.key === 'Escape') {
        if (this._isLogsPanelOpen()
          || !this.$.settingsPanel?.classList.contains('hidden')) {
          this.showPanel('main');
          return;
        }
        if (this.editingMessageId) {
          this._cancelMessageEdit();
          return;
        }
        this.closeLightbox();
        this.closeSidebar();
      }
    });

    WebChatComposer.initFileHandlers(this);

    this._onDocumentClickCancelDelete = (e) => {
      if (this._pendingDeleteConvId) {
        if (!e.target.closest(`.conv-item-delete[data-id="${this._pendingDeleteConvId}"]`)) {
          this._cancelPendingDelete();
        }
      }
      if (this._pendingDeleteMessageId) {
        const deleteSel = `.message-row[data-message-id="${CSS.escape(this._pendingDeleteMessageId)}"] .msg-action-btn[data-action="delete"]`;
        if (!e.target.closest(deleteSel)) {
          this._cancelPendingMessageDelete();
        }
      }
      if (this._pendingTrashDeleteId) {
        if (!e.target.closest(`.conv-trash-delete[data-id="${this._pendingTrashDeleteId}"]`)) {
          this._cancelPendingTrashDelete();
        }
      }
      if (this._pendingEmptyTrash && !e.target.closest('#conv-trash-empty-all')) {
        this._cancelPendingEmptyTrash();
      }
    };
    document.addEventListener('click', this._onDocumentClickCancelDelete);

    window.addEventListener('resize', () => this._syncFloatingSettingsVisibility());
    this._syncFloatingSettingsVisibility();

    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.currentConvId) {
        WebChatComposer.saveDraft(this, this.currentConvId);
        this._saveScrollPosition(this.currentConvId);
      } else if (!document.hidden) {
        void this._tickGlobalSync();
      }
    });
    window.addEventListener('pagehide', () => {
      if (this.currentConvId) {
        WebChatComposer.saveDraft(this, this.currentConvId);
        this._saveScrollPosition(this.currentConvId);
      }
    });
    window.addEventListener('pageshow', (ev) => {
      if (!this.currentConvId) return;
      WebChatComposer.restoreDraft(this, this.currentConvId);
      if (ev.persisted) {
        this._restoreScrollPosition(this.currentConvId);
      }
    });
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
      window.TaskNotifications?.updateSettingsUi?.(this.$.settingsPanel);
      void WebChatAuth.refreshAdminUsersList(this);
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

  _syncFloatingSettingsVisibility() {
    const bar = this.$.floatingSettings;
    if (!bar) return;
    const mobile = window.matchMedia('(max-width: 768px)').matches;
    const sidebarOpen = this.$.convSidebar.classList.contains('open');
    const visible = !mobile || sidebarOpen;
    bar.setAttribute('aria-hidden', visible ? 'false' : 'true');
    if (!visible && bar.contains(document.activeElement)) {
      document.activeElement.blur();
    }
  }

  _isMobileLayout() {
    return window.matchMedia('(max-width: 768px)').matches;
  }

  _sidebarSwipeBlocked() {
    if (!this._isMobileLayout()) return true;
    if (!this.$.settingsPanel?.classList.contains('hidden')) return true;
    if (!this.$.logsPanel?.classList.contains('hidden')) return true;
    if (!this.$.lightbox?.classList.contains('hidden')) return true;
    return false;
  }

  _sidebarIsOpen() {
    return this.$.convSidebar?.classList.contains('open') ?? false;
  }

  _getSidebarSheetWidth() {
    const sheet = this.$.convSidebarSheet;
    if (!sheet) return 280;
    return sheet.getBoundingClientRect().width || 280;
  }

  _resetSidebarDragStyles() {
    const sheet = this.$.convSidebarSheet;
    if (sheet) {
      sheet.style.removeProperty('transform');
      sheet.style.removeProperty('transition');
    }
    this.$.backdrop?.style.removeProperty('opacity');
    this.$.backdrop?.style.removeProperty('transition');
    this.$.backdrop?.classList.remove('is-dragging');
    this.$.convSidebar?.classList.remove('is-dragging');
    this._sidebarSwipe = null;
  }

  _setSidebarDragOffset(px) {
    const sheet = this.$.convSidebarSheet;
    if (!sheet) return;
    const width = this._getSidebarSheetWidth();
    const clamped = Math.max(-width, Math.min(0, px));
    sheet.style.transform = `translateX(${clamped}px)`;
    const progress = (width + clamped) / width;
    if (this.$.backdrop) {
      this.$.backdrop.classList.remove('hidden');
      this.$.backdrop.style.opacity = String(progress * 0.45);
    }
  }

  _snapSidebarDrag(open) {
    const sheet = this.$.convSidebarSheet;
    if (!sheet) return;
    sheet.style.transition = 'transform 0.28s cubic-bezier(0.4, 0, 0.2, 1)';
    sheet.style.transform = open ? 'translateX(0)' : `translateX(-${this._getSidebarSheetWidth()}px)`;
    if (this.$.backdrop) {
      this.$.backdrop.style.transition = 'opacity 0.28s cubic-bezier(0.4, 0, 0.2, 1)';
      this.$.backdrop.style.opacity = open ? '0.45' : '0';
    }
    const onEnd = () => {
      sheet.removeEventListener('transitionend', onEnd);
      this._resetSidebarDragStyles();
      if (open) this.openSidebar();
      else this.closeSidebar();
    };
    sheet.addEventListener('transitionend', onEnd);
    setTimeout(onEnd, 320);
  }

  _bindSidebarSwipeGestures() {
    const OPEN_ZONE_RATIO = 0.42;
    const MIN_DRAG_PX = 12;
    const COMMIT_RATIO = 0.32;

    const onTouchStart = (e) => {
      if (this._sidebarSwipeBlocked() || e.touches.length !== 1) return;
      const t = e.touches[0];
      const target = e.target;
      if (target.closest('.conv-sidebar-sheet') && this._sidebarIsOpen()) {
        if (target.closest('input, textarea, select, button, a, .conv-item-delete')) return;
      }
      const open = this._sidebarIsOpen();
      let mode = null;
      if (!open && t.clientX <= window.innerWidth * OPEN_ZONE_RATIO) {
        mode = 'open';
      } else if (open) {
        mode = 'close';
      }
      if (!mode) return;
      this._sidebarSwipe = {
        mode,
        startX: t.clientX,
        startY: t.clientY,
        dragging: false,
        width: this._getSidebarSheetWidth(),
      };
    };

    const onTouchMove = (e) => {
      const s = this._sidebarSwipe;
      if (!s || e.touches.length !== 1) return;
      const dx = e.touches[0].clientX - s.startX;
      const dy = e.touches[0].clientY - s.startY;
      if (!s.dragging) {
        if (Math.abs(dx) < MIN_DRAG_PX || Math.abs(dx) < Math.abs(dy) * 1.15) return;
        if (s.mode === 'open' && dx <= 0) return;
        if (s.mode === 'close' && dx >= 0) return;
        s.dragging = true;
        this.$.convSidebar?.classList.add('is-dragging');
        this.$.backdrop?.classList.add('is-dragging');
        if (s.mode === 'open' && !this._sidebarIsOpen()) {
          this.$.backdrop?.classList.remove('hidden');
        }
      }
      e.preventDefault();
      const offset = s.mode === 'open'
        ? -s.width + dx
        : dx;
      this._setSidebarDragOffset(offset);
    };

    const onTouchEnd = (e) => {
      const s = this._sidebarSwipe;
      if (!s) return;
      if (!s.dragging) {
        this._sidebarSwipe = null;
        return;
      }
      const dx = e.changedTouches[0].clientX - s.startX;
      const progress = s.mode === 'open'
        ? Math.max(0, Math.min(1, dx / s.width))
        : Math.max(0, Math.min(1, (s.width + dx) / s.width));
      const shouldOpen = progress >= COMMIT_RATIO;
      this._snapSidebarDrag(shouldOpen);
    };

    const onTouchCancel = () => {
      if (!this._sidebarSwipe?.dragging) {
        this._sidebarSwipe = null;
        return;
      }
      this._snapSidebarDrag(this._sidebarIsOpen());
    };

    document.addEventListener('touchstart', onTouchStart, { passive: true });
    document.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('touchend', onTouchEnd, { passive: true });
    document.addEventListener('touchcancel', onTouchCancel, { passive: true });
  }

  openSidebar() {
    this._resetSidebarDragStyles();
    this.$.convSidebar.classList.add('open');
    this.$.backdrop.classList.remove('hidden');
    requestAnimationFrame(() => {
      this.$.backdrop.classList.add('visible');
      this._updateConvTitleTooltips();
    });
    document.body.style.overflow = 'hidden';
    this._syncFloatingSettingsVisibility();
  }

  closeSidebar() {
    this._resetSidebarDragStyles();
    this.$.convSidebar.classList.remove('open');
    this.$.backdrop.classList.remove('visible');
    setTimeout(() => this.$.backdrop.classList.add('hidden'), 300);
    document.body.style.overflow = '';
    this._syncFloatingSettingsVisibility();
  }

  setConnStatus(state, labelOverride) {
    this.$.connStatus.className = `conn-status ${state}`;
    const labels = {
      connected: 'Подключено',
      connecting: 'Подключение…',
      disconnected: 'Офлайн',
    };
    const label = labelOverride || labels[state] || '—';
    this.$.connStatus.title = label;
    if (this.$.connStatusLabel) this.$.connStatusLabel.textContent = label;
  }

  /** Подпись этапа/инструмента для пузыря ассистента (без сырых имён tools). */
  _resolveProgressLabel(text, opts = {}) {
    const toolLabels = window.TOOL_USER_LABELS || {};
    const stageLabels = window.PROGRESS_STAGE_LABELS || {};
    if (opts.tool && toolLabels[opts.tool]) {
      return toolLabels[opts.tool];
    }
    if (opts.stage && stageLabels[opts.stage]) {
      return stageLabels[opts.stage];
    }
    const raw = (text || '').trim();
    if (raw && !/^[a-z][a-z0-9_]*$/i.test(raw)) {
      return raw;
    }
    return 'Выполняется…';
  }

  _setUiActivityStage(stage) {
    this._uiActivityStage = stage || null;
    this._recomputeUiState();
  }

  _recomputeUiState() {
    const rs = this.socket?.ws?.readyState;
    const wsOpen = rs === WebSocket.OPEN;
    const wsConnecting = rs === WebSocket.CONNECTING;

    let state = CHAT_UI_STATE.READY;
    if (this._wsOfflineBannerShown) {
      state = CHAT_UI_STATE.OFFLINE;
    } else if (this._wsReconnecting || (wsConnecting && !wsOpen)) {
      state = CHAT_UI_STATE.RECONNECTING;
    } else if (this.streaming || this._generationResumeActive) {
      state = (
        this._uiActivityStage
        && TOOL_ACTIVITY_STAGES.has(this._uiActivityStage)
      )
        ? CHAT_UI_STATE.TOOL_RUNNING
        : CHAT_UI_STATE.STREAMING;
    } else if (this.currentConvId && !wsOpen) {
      state = CHAT_UI_STATE.CONNECTING;
    }

    this._uiState = state;
    document.body.dataset.chatUiState = state;
    this._applyUiStateConnPill(state);
  }

  _applyUiStateConnPill(state) {
    const connLabels = {
      [CHAT_UI_STATE.OFFLINE]: ['disconnected', 'Офлайн'],
      [CHAT_UI_STATE.RECONNECTING]: ['connecting', 'Переподключение…'],
      [CHAT_UI_STATE.CONNECTING]: ['connecting', 'Подключение…'],
      [CHAT_UI_STATE.TOOL_RUNNING]: ['connected', 'Выполнение задачи…'],
      [CHAT_UI_STATE.STREAMING]: ['connected', 'Генерация ответа…'],
      [CHAT_UI_STATE.READY]: ['connected', 'Подключено'],
      [CHAT_UI_STATE.ERROR]: ['disconnected', 'Ошибка'],
    };
    const [conn, label] = connLabels[state] || connLabels[CHAT_UI_STATE.READY];
    this.setConnStatus(conn, label);
  }

  _isMessagesSkeletonVisible() {
    return Boolean(this.$.chatMessages?.querySelector('.messages-skeleton'));
  }

  _showMessagesSkeleton() {
    if (!this.$.chatMessages) return;
    this._messagesLoading = true;
    this.$.chatMessages.classList.add('is-loading');
    const wrap = document.createElement('div');
    wrap.className = 'messages-skeleton';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.setAttribute('aria-busy', 'true');
    for (let i = 0; i < MESSAGE_SKELETON_ROWS; i += 1) {
      wrap.appendChild(this._createMessageSkeletonRow(i % 2 === 0));
    }
    this.$.chatMessages.replaceChildren(wrap);
  }

  _createMessageSkeletonRow(isUser) {
    const row = document.createElement('div');
    row.className = `message-skeleton-row${isUser ? ' user' : ' assistant'}`;
    const bubble = document.createElement('div');
    bubble.className = 'message-skeleton-bubble';
    row.appendChild(bubble);
    return row;
  }

  async _syncAfterReconnect() {
    if (!this.currentConvId) return;
    try {
      const status = await this.api(
        `/api/conversations/${this.currentConvId}/generation-status`,
      );
      if (status.in_progress) {
        if (!this.streamEl || !this._generationResumeActive) {
          await this._resumeOngoingGeneration(status);
        } else {
          await this._refreshStreamingBubbleFromServer(status);
          this._syncResumeProgress(status);
          if (!this._generationWatchRunning) {
            this._watchGenerationUntilDone();
          }
        }
        return;
      }
      if (this.streaming || this._generationResumeActive) {
        await this.loadMessages({ force: true, preserveScroll: true });
        this._completeGenerationUi({ preserveScroll: true }).catch(() => {});
      }
    } catch (err) {
      this.log?.warn('ws', `Синхронизация после reconnect: ${err.message}`);
    }
  }

  onAck(msg) {
    if (this._regenerating) return;
    const userMessageId = msg?.user_message_id;
    if (!userMessageId) return;
    const rows = this.$.chatMessages.querySelectorAll('.message-row.user:not([data-message-id])');
    const last = rows[rows.length - 1];
    if (last) {
      last.dataset.messageId = userMessageId;
      this._syncMessageActions(last, 'user');
      return;
    }
    if (!this.streaming) {
      void this.loadMessages();
    }
  }

  async api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', ...(options.headers || {}) },
      ...options,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? body;
      } catch { /* ignore */ }
      const msg = formatApiErrorDetail(detail) || 'Ошибка API';
      this.log?.error('api', `${method} ${path} → ${res.status}`, msg);
      throw new Error(msg);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  async loadPresets() {
    return WebChatPresets.load(this);
  }

  _flushPresetDraftsToStorage() {
    WebChatPresets.flushDraftsToStorage(this);
  }

  _hasUnsyncedPresetDrafts() {
    return WebChatPresets.hasUnsyncedDrafts(this);
  }

  populateConvPresetSelect(selectedId) {
    WebChatPresets.populateConvSelect(this, selectedId);
  }

  async savePresetPromptIfDirty(options = {}) {
    return WebChatPresets.savePromptIfDirty(this, options);
  }

  async onPresetSelectChange() {
    return WebChatPresets.onSelectChange(this);
  }

  async onChatPresetChange() {
    return WebChatPresets.onChatPresetChange(this);
  }

  async savePresetPrompt(options = {}) {
    return WebChatPresets.savePrompt(this, options);
  }

  async setDefaultPreset() {
    return WebChatPresets.setDefault(this);
  }

  _onPresetPromptInput() {
    WebChatPresets.onPromptInput(this);
  }

  syncPresetPromptField() {
    WebChatPresets.syncPromptField(this);
  }

  _updateChatPresetToolbar() {
    WebChatPresets.updateChatToolbar(this);
  }

  async loadConversations() {
    return WebChatConversations.load(this);
  }

  async loadTrash() {
    return WebChatConversations.loadTrash(this);
  }

  renderConvList() {
    WebChatConversations.renderList(this);
  }

  _conversationsFingerprintFrom(conversations) {
    return WebChatConversations.fingerprintFrom(conversations);
  }

  async _syncConversationsFromServer() {
    return WebChatConversations.syncFromServer(this);
  }

  _setSidebarTab(tab) {
    WebChatConversations.setSidebarTab(this, tab);
  }

  _toggleTrashPanel() {
    WebChatConversations.toggleTrashPanel(this);
  }

  _closeConvSearchPanel() {
    WebChatConversations.closeSearchPanel(this);
  }

  _cancelPendingDelete() {
    WebChatConversations.cancelPendingDelete(this);
  }

  _updateConvTitleTooltips() {
    WebChatConversations.updateTitleTooltips(this);
  }

  async createConversation() {
    return WebChatConversations.create(this);
  }

  _upsertConversationInList(conv) {
    WebChatConversations.upsertInList(this, conv);
  }

  _startGlobalSync() {
    this._stopGlobalSync();
    const tick = () => {
      void this._tickGlobalSync().finally(() => {
        if (this._globalSyncTimer !== null) {
          this._globalSyncTimer = setTimeout(tick, this._globalSyncIntervalMs);
        }
      });
    };
    this._globalSyncTimer = setTimeout(tick, this._globalSyncIntervalMs);
  }

  _stopGlobalSync() {
    if (this._globalSyncTimer) {
      clearTimeout(this._globalSyncTimer);
      this._globalSyncTimer = null;
    }
  }

  async _tickGlobalSync() {
    if (document.hidden) return;
    await this._syncConversationsFromServer();
    await this._syncActiveConversationFromServer();
  }

  _messagesFingerprintFromList(messages) {
    if (!messages?.length) return '0';
    const last = messages[messages.length - 1];
    const streaming = messages.some(
      (m) => m.role === 'assistant' && m.content_json?.streaming,
    );
    const cj = last.content_json || {};
    const imgN = (cj.images || []).length + (cj.image_asset_ids || []).length;
    return `${messages.length}:${last.id}:${last.role}:${streaming ? 1 : 0}:${(last.content_text || '').length}:${imgN}`;
  }

  _messagesStructureKey(messages) {
    if (!messages?.length) return '';
    return messages.map((m) => m.id).join('|');
  }

  _messageContentFingerprint(m) {
    const cj = m.content_json || {};
    const imgN = (cj.images || []).length + (cj.image_asset_ids || []).length;
    const ragN = Array.isArray(cj.rag_sources) ? cj.rag_sources.length : 0;
    const reasoningLen = typeof cj.reasoning === 'string' ? cj.reasoning.length : 0;
    return [
      m.role,
      cj.streaming ? '1' : '0',
      (m.content_text || '').length,
      imgN,
      cj.turn_phase || '',
      ragN,
      reasoningLen,
    ].join(':');
  }

  _activeStreamingIdFromList(messages) {
    const streamingAssistantIds = (messages || [])
      .filter((m) => m.role === 'assistant' && m.content_json?.streaming)
      .map((m) => m.id);
    return streamingAssistantIds.length
      ? streamingAssistantIds[streamingAssistantIds.length - 1]
      : null;
  }

  _domMessageIds() {
    return [...this.$.chatMessages.querySelectorAll('.message-row[data-message-id]')]
      .map((row) => row.dataset.messageId)
      .filter(Boolean);
  }

  _tagMessageRow(row, m) {
    if (row && m?.id) {
      row.dataset.contentFp = this._messageContentFingerprint(m);
    }
    return row;
  }

  _patchMessageRowIfNeeded(m, { activeStreamingId = null } = {}) {
    const row = this._findRow(m.id);
    if (!row) return false;
    const fp = this._messageContentFingerprint(m);
    if (row.dataset.contentFp === fp) return false;
    const wasStreamRow = row === this.streamRow
      || (m.id && this.streamRow?.dataset?.messageId === m.id);
    const newRow = this._tagMessageRow(
      this._messageRowFromDb(m, { activeStreamingId }),
      m,
    );
    row.replaceWith(newRow);
    if (wasStreamRow && (this.streaming || this._generationResumeActive)) {
      this._applyStreamUI(newRow);
    }
    return true;
  }

  /** Держать streamRow/streamImagesEl на строке в DOM (после patch/dedupe). */
  _ensureStreamBoundToMessageId(messageId) {
    if (!messageId) return false;
    const row = this._findRow(messageId);
    if (!row) return false;
    const inDoc = this.streamRow && this.$.chatMessages?.contains(this.streamRow);
    const sameId = this.streamRow?.dataset?.messageId === messageId;
    if (inDoc && sameId) return true;
    if (this.streaming || this._generationResumeActive) {
      this._applyStreamUI(row);
      return true;
    }
    return false;
  }

  async _syncAssistantImagesToDom(messageId) {
    if (!messageId || !this.currentConvId) return;
    const row = this._findRow(messageId);
    const msgEl = row?.querySelector('.chat-message.assistant');
    if (!msgEl) return;
    try {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const target = messages.find((m) => m.id === messageId);
      if (!target) return;
      const urls = WebChatMessages.imageUrlsFromMessage(target);
      if (!urls.length) return;
      let grid = msgEl.querySelector('.message-images');
      if (!grid) {
        grid = document.createElement('div');
        grid.className = 'message-images';
        const status = msgEl.querySelector('.message-status');
        if (status) msgEl.insertBefore(grid, status);
        else msgEl.appendChild(grid);
      }
      const domKeys = new Set(
        [...grid.querySelectorAll('img')].map((img) => WebChatMessages.imageUrlKey(
          img.dataset.url || img.getAttribute('src'),
        )),
      );
      const missing = urls.some((u) => {
        const key = WebChatMessages.imageUrlKey(u);
        return key && !domKeys.has(key);
      });
      if (missing || grid.children.length < urls.length) {
        WebChatMessages.setGridImages(this, grid, urls);
        WebChatMessages.syncAssistantLayoutClasses(this, msgEl);
      }
    } catch (err) {
      this.log?.warn('chat', `sync images: ${err.message}`);
    }
  }

  _beginConvSwitchOverlay() {
    clearTimeout(this._convSwitchOverlayTimer);
    this._convSwitchOverlayTimer = setTimeout(() => {
      this.$.loadingOverlay?.classList.remove('hidden');
    }, CONV_SWITCH_OVERLAY_DELAY_MS);
  }

  _endConvSwitchOverlay() {
    clearTimeout(this._convSwitchOverlayTimer);
    this._convSwitchOverlayTimer = null;
    this.$.loadingOverlay?.classList.add('hidden');
  }

  async _messagesFingerprintFromServer() {
    if (!this.currentConvId) return '';
    const messages = await this.api(
      `/api/conversations/${this.currentConvId}/messages?limit=30`,
    );
    return this._messagesFingerprintFromList(messages);
  }

  async _syncActiveConversationFromServer() {
    if (!this.currentConvId) return;
    try {
      const status = await this.api(
        `/api/conversations/${this.currentConvId}/generation-status`,
      );
      const msgFp = await this._messagesFingerprintFromServer();
      const messagesChanged = msgFp !== this._messagesFingerprint;

      if (status.in_progress) {
        if (!this.streaming && !this._generationResumeActive) {
          await this._resumeOngoingGeneration(status);
        } else {
          const tempPlaceholder = this.streamRow?.hasAttribute('data-temp')
            && !this.streamRow?.dataset?.messageId;
          if (!tempPlaceholder) {
            await this._refreshStreamingBubbleFromServer(status);
          }
          this._syncResumeProgress(status);
          if (!this._generationWatchRunning) this._watchGenerationUntilDone();
        }
        this._messagesFingerprint = msgFp;
        return;
      }

      if (this._generationResumeActive || this.streaming) {
        this._generationResumeActive = false;
        await this._completeGenerationUi({ preserveScroll: !this._scrollStuckToBottom });
        this._messagesFingerprint = await this._messagesFingerprintFromServer();
        return;
      }

      if (messagesChanged) {
        await this.loadMessages({ preserveScroll: !this._scrollStuckToBottom });
        this._messagesFingerprint = msgFp;
      }
    } catch (err) {
      this.log?.warn('sync', err.message);
    }
  }

  _highlightMessage(messageId) {
    const row = this._findRow(messageId);
    if (!row) return;
    row.classList.add('search-highlight');
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => row.classList.remove('search-highlight'), 2600);
    if (this.currentConvId) {
      setTimeout(() => this._saveScrollPosition(this.currentConvId), 350);
    }
  }

  exportCurrentConversation() {
    if (!this.currentConvId) return;
    window.location.assign(`/api/conversations/${this.currentConvId}/export`);
  }

  _updateExportButton() {
    if (!this.$.exportConversationBtn) return;
    this.$.exportConversationBtn.disabled = !this.currentConvId;
  }

  _clearCurrentConversation() {
    if (this.currentConvId) WebChatComposer.clearDraft(this, this.currentConvId);
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
    WebChatComposer.resetUi(this);
    this.$.userInput.disabled = true;
    this.$.sendBtn.disabled = true;
    if (this.$.macroInsertBtn) this.$.macroInsertBtn.disabled = true;
    if (this.$.macroInsertMenuBtn) this.$.macroInsertMenuBtn.disabled = true;
    if (this.$.macroContextFullBtn) this.$.macroContextFullBtn.disabled = true;
    if (this.$.documentRagBtn) this.$.documentRagBtn.disabled = true;
    if (this.$.composerMoreBtn) this.$.composerMoreBtn.disabled = true;
    if (this.$.preloadModelsBtn) this.$.preloadModelsBtn.disabled = true;
    WebChatComposer.closeToolsMenu(this);
    this._hideRagPreview();
    if (this.streaming) this.endStreaming();
    WebChatComposer.syncSendState(this);
  }

  _bindDialogFocusTrap(dialog) {
    if (!dialog || dialog.dataset.focusTrapBound) return;
    dialog.dataset.focusTrapBound = '1';
    const selector = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
    dialog.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab' || !dialog.open) return;
      const nodes = [...dialog.querySelectorAll(selector)].filter(
        (el) => !el.disabled && el.offsetParent !== null,
      );
      if (nodes.length < 2) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }

  async _fetchConversationMeta(id, opts = {}) {
    const prefetched = opts.prefetchedConversation;
    if (prefetched && prefetched.id === id) {
      return prefetched;
    }
    const maxAttempts = opts.retryOn404 ? 4 : 1;
    let lastErr;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      try {
        return await this.api(`/api/conversations/${id}`);
      } catch (err) {
        lastErr = err;
        const retryable = /не найдена/i.test(err?.message || '');
        if (!retryable || attempt >= maxAttempts - 1) {
          throw err;
        }
        await new Promise((resolve) => {
          setTimeout(resolve, 60 * (attempt + 1));
        });
      }
    }
    throw lastErr;
  }

  async selectConversation(id, opts = {}) {
    const rs = this.socket?.ws?.readyState;
    if (
      this.currentConvId === id
      && (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING)
      && !opts.scrollToMessageId
    ) {
      return;
    }

    if (this.currentConvId && this.currentConvId !== id) {
      WebChatComposer.saveDraft(this, this.currentConvId);
      this._saveScrollPosition(this.currentConvId);
    }

    this._setSidebarTab('conversations');
    this._cancelPendingDelete();
    this._cancelPendingMessageDelete();
    this._closeConvSearchPanel();
    this.disconnectSocket();
    this.log?.info('chat', `Беседа ${id}`);
    const prevConvId = this.currentConvId && this.currentConvId !== id
      ? this.currentConvId
      : null;
    this.currentConvId = id;
    localStorage.setItem('webchat_conv_id', id);

    this._beginConvSwitchOverlay();
    try {
      this.currentConv = await this._fetchConversationMeta(id, opts);

      this._setSettingsChatTitle(this.currentConv.title);
      this.populateConvPresetSelect(this.currentConv.preset_id);
      WebChatImg2imgPreset?.refreshPresetCache?.(this);
      WebChatImg2imgPreset?.logDiagnostics?.(this, 'conversation_selected');

      this.$.placeholder.classList.add('hidden');
      this.$.chatHistory.classList.remove('hidden');
      this._updateChatPresetToolbar();
      this.$.chatComposer.classList.remove('hidden');
      this.$.userInput.disabled = false;
      this.$.sendBtn.disabled = false;
      if (this.$.macroInsertBtn) this.$.macroInsertBtn.disabled = false;
    if (this.$.macroInsertMenuBtn) this.$.macroInsertMenuBtn.disabled = false;
      if (this.$.macroContextFullBtn) this.$.macroContextFullBtn.disabled = false;
      if (this.$.documentRagBtn) this.$.documentRagBtn.disabled = false;
      if (this.$.composerMoreBtn) this.$.composerMoreBtn.disabled = false;
      if (this.$.preloadModelsBtn) this.$.preloadModelsBtn.disabled = false;

      WebChatComposer.resetUi(this);
      this._updateDocumentRagToggleUi();
      this._scheduleRagPreview();
      this.connectSocket();

      const scrollToMessageId = opts.scrollToMessageId || null;
      let generationStatus = null;
      try {
        generationStatus = await this.api(
          `/api/conversations/${id}/generation-status`,
        );
      } catch (err) {
        this.log?.warn('chat', err.message || 'generation-status');
      }
      const loadOpts = {
        activeStreamingId: generationStatus?.streaming_message_id || null,
      };
      try {
        if (scrollToMessageId) {
          this._scrollStuckToBottom = false;
          await this.loadMessages({
            scrollToEnd: false,
            preserveScroll: false,
            ...loadOpts,
          });
          this._highlightMessage(scrollToMessageId);
        } else {
          const scrollEntry = this._getScrollPositionEntry(id);
          const restoreScroll = scrollEntry
            && !scrollEntry.atBottom
            && (scrollEntry.anchorMessageId || Number.isFinite(scrollEntry.scrollTop));
          this._scrollStuckToBottom = !restoreScroll;
          await this.loadMessages({
            scrollToEnd: !restoreScroll,
            preserveScroll: false,
            restoreScrollEntry: restoreScroll ? scrollEntry : null,
            ...loadOpts,
          });
        }

        await this._resumeOngoingGeneration(generationStatus || {});
        const serverRestored = WebChatComposer.restoreServerDraft(this, this.currentConv);
        if (!serverRestored) {
          WebChatComposer.restoreDraft(this, id);
          WebChatComposer.restorePendingFromSession(this);
        }
        this.renderConvList();
      } catch (err) {
        this.showError(err.message || 'Не удалось загрузить беседу');
        this.log?.error('chat', err.message || 'selectConversation failed');
      }
      WebChatComposer.syncSendState(this);
    } catch (err) {
      if (prevConvId && prevConvId !== id) {
        this.currentConvId = prevConvId;
        localStorage.setItem('webchat_conv_id', prevConvId);
      } else {
        this.currentConvId = null;
        this.currentConv = null;
        localStorage.removeItem('webchat_conv_id');
        this.$.placeholder.classList.remove('hidden');
        this.$.chatHistory.classList.add('hidden');
        this.$.chatComposer.classList.add('hidden');
      }
      const authHint = (err?.message || '').includes('Требуется вход');
      this.showError(
        authHint
          ? 'Войдите в web-chat под тем же пользователем, что использует галерея (например, admin).'
          : (err.message || 'Не удалось открыть беседу'),
        authHint ? 10000 : 8000,
      );
      this.log?.error('chat', err.message || 'selectConversation failed');
    } finally {
      this._endConvSwitchOverlay();
    }
  }

  _userMessageDisplayText(m) {
    return WebChatMessages.userDisplayText(this, m);
  }

  async loadMessages(opts = {}) {
    return WebChatMessages.load(this, opts);
  }

  appendMessageFromDb(m) {
    WebChatMessages.appendFromDb(this, m);
  }

  _messageRowFromDb(m, opts = {}) {
    return WebChatMessages.rowFromDb(this, m, opts);
  }

  _syncAssistantLayoutClasses(el) {
    WebChatMessages.syncAssistantLayoutClasses(this, el);
  }

  _ensureAssistantStreamShell(el) {
    return WebChatMessages.ensureAssistantStreamShell(this, el);
  }

  _fillAssistantBubble(el, text, imageUrls, reasoning = null) {
    WebChatMessages.fillAssistantBubble(this, el, text, imageUrls, reasoning);
  }

  appendAssistantDraftFromDb(m, imageUrls = null) {
    WebChatMessages.appendAssistantDraftRow(this, m, imageUrls);
  }

  _buildAssistantDraftRow(m, imageUrls = null) {
    return WebChatMessages.buildAssistantDraftRow(this, m, imageUrls);
  }

  _findRow(messageId) {
    return WebChatMessages.findRow(this, messageId);
  }

  _removeFollowingRows(fromRow, includeFrom) {
    WebChatMessages.removeFollowingRows(this, fromRow, includeFrom);
  }

  addUserBubble(text, messageId = null, imageUrls = []) {
    WebChatMessages.addUserBubble(this, text, messageId, imageUrls);
  }

  _buildUserRow(text, messageId = null, imageUrls = []) {
    return WebChatMessages.buildUserRow(this, text, messageId, imageUrls);
  }

  addAssistantBubble(text, imageUrls, messageId = null) {
    WebChatMessages.addAssistantBubble(this, text, imageUrls, messageId);
  }

  _buildAssistantRow(text, imageUrls, messageId = null, ragHits = null, reasoning = null) {
    return WebChatMessages.buildAssistantRow(this, text, imageUrls, messageId, ragHits, reasoning);
  }

  async _attachRagSourcesAfterTurn(assistantMessageId) {
    return WebChatMessages.attachRagSourcesAfterTurn(this, assistantMessageId);
  }

  async copyMessageText(messageId, role, copyBtn) {
    return WebChatMessages.copyMessageText(this, messageId, role, copyBtn);
  }

  async editMessage(messageId, role) {
    return WebChatMessages.editMessage(this, messageId, role);
  }

  async regenerateMessage(messageId) {
    return WebChatMessages.regenerateMessage(this, messageId);
  }

  async _runRegenerate(messageId, opts = {}) {
    return WebChatMessages.runRegenerate(this, messageId, opts);
  }

  _setGridImages(grid, urls) {
    return WebChatMessages.setGridImages(this, grid, urls);
  }

  _appendImagesToGrid(grid, urls) {
    return WebChatMessages.appendImagesToGrid(this, grid, urls);
  }

  _syncStreamImagesFromServer(urls) {
    WebChatMessages.syncStreamImagesFromServer(this, urls);
  }

  _createImage(url, opts = {}) {
    return WebChatMessages.createImage(this, url, opts);
  }

  _bindMessageImageActions() {
    WebChatMessages.bindMessageImageActions(this);
  }

  _syncAllMessageActions() {
    WebChatMessages.syncAllMessageActions(this);
  }

  _syncMessageActions(row, role) {
    WebChatMessages.syncMessageActions(this, row, role);
  }

  _attachActions(row, role) {
    WebChatMessages.attachActions(this, row, role);
  }

  _cancelPendingMessageDelete() {
    WebChatMessages.cancelPendingMessageDelete(this);
  }

  _cancelMessageImageDelete() {
    WebChatMessages.cancelMessageImageDelete(this);
  }

  connectSocket() {
    if (this.socket) {
      this.disconnectSocket();
    }
    this._wsReconnecting = false;
    this._recomputeUiState();
    this.log?.info('ws', `Подключение к беседе ${this.currentConvId}`);
    this.socket = new ChatSocket(this.currentConvId, {
      onConnecting: () => {
        this._wsReconnecting = false;
        this._recomputeUiState();
      },
      onOpen: () => {
        this._wsReconnecting = false;
        if (this._wsOfflineBannerShown) {
          this.hideError();
          this._wsOfflineBannerShown = false;
        }
        this.log?.info('ws', 'Соединение установлено');
        if (this.streaming || this._generationResumeActive) {
          void this._syncAfterReconnect();
        }
        this._recomputeUiState();
        WebChatComposer.syncSendState(this);
      },
      onClose: () => {
        this.log?.warn('ws', 'Соединение закрыто');
        this._recomputeUiState();
      },
      onReconnecting: (delay, attempt, maxAttempts) => {
        this._wsReconnecting = true;
        this._recomputeUiState();
        this.log?.warn('ws', `Переподключение ${attempt}/${maxAttempts} через ${delay} мс`);
        if (this.streaming || this._generationResumeActive) {
          void this._syncAfterReconnect();
        }
      },
      onReconnectExhausted: (attempts, maxAttempts) => {
        this._wsReconnecting = false;
        this._wsOfflineBannerShown = true;
        this._recomputeUiState();
        this.showError(
          'Соединение с сервером потеряно. Проверьте сеть и попробуйте подключиться снова.',
          0,
          { showRetry: true },
        );
        this.log?.error('ws', `Переподключение остановлено (${attempts}/${maxAttempts})`);
      },
      onError: () => this.log?.error('ws', 'Ошибка WebSocket'),
      onTextDelta: (chunk) => this.onTextDelta(chunk),
      onReasoningDelta: (chunk) => this.onReasoningDelta(chunk),
      onImages: (urls) => this.onImages(urls),
      onToolStart: (name) => this.onToolStart(name),
      onToolDone: (name, summary, skipped) => this.onToolDone(name, summary, skipped),
      onProgress: (msg) => this.onProgress(msg),
      onGenerationUpdate: (msg) => this.onGenerationUpdate(msg),
      onAck: (msg) => this.onAck(msg),
      onDone: (msg) => this.onTurnDone(msg),
      onWsError: (message, code, errorId) => this.onWsError(message, code, errorId),
      onConnected: (msg) => this._onWsConnected(msg),
      onAssistantDraft: (msg) => this._onAssistantDraft(msg),
    });
    this.socket.connect();
  }

  disconnectSocket() {
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this._wsReconnecting = false;
    this.socket?.disconnect();
    this.socket = null;
    this._recomputeUiState();
    WebChatComposer.syncSendState(this);
  }

  async _ensureSocketReady(timeoutMs = 8000) {
    if (!this.currentConvId) return false;
    if (!this.socket) {
      this.connectSocket();
    } else {
      const state = this.socket.ws?.readyState;
      if (state !== WebSocket.OPEN && (state === WebSocket.CLOSED || state == null)) {
        this.socket.connect();
      }
    }
    if (this.socket?.ws?.readyState === WebSocket.OPEN) return true;

    return new Promise((resolve) => {
      const started = Date.now();
      const tick = () => {
        if (this.socket?.ws?.readyState === WebSocket.OPEN) {
          resolve(true);
          return;
        }
        if (Date.now() - started >= timeoutMs) {
          resolve(false);
          return;
        }
        setTimeout(tick, 80);
      };
      tick();
    });
  }

  async sendMessage() {
    const rawText = this.$.userInput?.value?.trim() ?? '';
    const blocked = WebChatComposer.sendBlockedReason(this, rawText);
    if (blocked) {
      if (blocked !== null) this.showError(blocked, 3500);
      return;
    }

    if (!(await WebChatPreloadModels.ensureBeforeSend(this))) {
      return;
    }

    if (!(await this._ensureSocketReady(25000))) {
      this.showError('Не удалось подключиться к серверу. Проверьте сеть или нажмите «Повторить».', 5000, { showRetry: true });
      return;
    }

    this._generationHadImages = false;
    if (this.editingMessageId) {
      await this._submitEdit(rawText);
      return;
    }

    const payloadText = typeof WebChatImg2imgPreset !== 'undefined'
      ? WebChatImg2imgPreset.getPayloadText(this, rawText)
      : rawText;
    if (typeof WebChatImg2imgPreset !== 'undefined') {
      WebChatImg2imgPreset.logDiagnostics?.(this, 'send_message', {
        rawLen: rawText.length,
        payloadLen: payloadText.length,
        willSendDisplayText: rawText !== payloadText,
      });
      if (
        WebChatImg2imgPreset.isPanelEnabled?.(this)
        && payloadText === rawText
        && WebChatImg2imgPreset.collectDiagnostics?.(this)?.hintFromFieldsOnly
      ) {
        this.log?.warn(
          'img2img-preset',
          'поля заполнены, но префикс не добавлен — см. диагностику выше',
        );
      }
    }
    const ids = this.pendingAttachments.map((a) => a.id);
    const pendingImages = this.pendingAttachments
      .map((a) => mediaFullUrl(a.preview_url))
      .filter(Boolean);
    this.addUserBubble(rawText, null, pendingImages);
    WebChatComposer.resetUi(this);
    WebChatComposer.clearDraft(this, this.currentConvId);
    this.startStreaming();

    try {
      this.socket.sendUserMessage(payloadText, ids, this.getWsIntegrationPayload(), rawText);
    } catch (err) {
      this.showError(err.message);
      this.endStreaming();
    }
  }

  async _submitEdit(text) {
    const messageId = this.editingMessageId;
    const role = this.editingRole || 'user';
    const row = this._findRow(messageId);
    const pendingForUser = role === 'user' ? [...this.pendingAttachments] : [];
    try {
      this.log?.info('msg', `Редактирование ${role} ${messageId}`);
      const body = { content_text: text };
      if (role === 'user') {
        body.attachment_ids = pendingForUser.map((a) => a.id);
      }
      await this.api(`/api/conversations/${this.currentConvId}/messages/${messageId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      this._exitMessageEditUi();
      WebChatComposer.resetUi(this);

      if (role === 'user') {
        if (row) {
          const textEl = row.querySelector('.user-text');
          if (textEl) {
            textEl.dataset.rawText = text;
            this.promptMacros.renderUserText(textEl, text);
          } else if (text.trim()) {
            const el = row.querySelector('.chat-message.user') || row.querySelector('.chat-message');
            if (el) {
              const textElNew = document.createElement('div');
              textElNew.className = 'user-text';
              textElNew.dataset.rawText = text;
              this.promptMacros.renderUserText(textElNew, text);
              const grid = el.querySelector('.message-images');
              if (grid) el.insertBefore(textElNew, grid);
              else el.prepend(textElNew);
            }
          }
          this._updateUserRowImages(row, pendingForUser);
          this._removeFollowingRows(row, false);
        }
        WebChatComposer.clearDraft(this, this.currentConvId);
        await this._runRegenerate(messageId);
      } else if (row) {
        const mb = row.querySelector('.message-bubble');
        if (mb) mb.innerHTML = formatMarkdown(text);
      }
    } catch (err) {
      this.showError(err.message);
      if (/не найдено|not found/i.test(err.message)) {
        this.log?.warn('msg', 'Сообщение не найдено на сервере — перезагрузка истории');
        this._exitMessageEditUi();
        WebChatComposer.resetUi(this);
        if (this.currentConvId) WebChatComposer.restoreDraft(this, this.currentConvId);
        await this.loadMessages();
      }
    }
  }

  _exitMessageEditUi() {
    const row = this.editingMessageId ? this._findRow(this.editingMessageId) : null;
    row?.classList.remove('is-being-edited');
    row?.querySelector('.message-images')?.classList.remove('hidden');
    this.editingMessageId = null;
    this.editingRole = null;
    this._editComposerBackup = null;
    this.$.userInput.placeholder = this._inputPlaceholderDefault;
  }

  _cancelMessageEdit() {
    if (!this.editingMessageId) return;
    const backup = this._editComposerBackup;
    this._exitMessageEditUi();
    WebChatComposer.resetUi(this);

    if (backup) {
      this.$.userInput.value = backup.text;
      WebChatComposer.autoResizeInput(this);
      for (const att of backup.attachments) {
        this.pendingAttachments.push(att);
        WebChatComposer.renderAttachmentChip(this, att);
      }
      if (this.pendingAttachments.length) {
        this.$.attachmentStrip.classList.remove('hidden');
      }
    } else if (this.currentConvId) {
      WebChatComposer.restoreDraft(this, this.currentConvId);
    }
  }

  _updateUserRowImages(row, attachments) {
    const el = row.querySelector('.chat-message.user') || row.querySelector('.chat-message');
    if (!el) return;
    const urls = attachments
      .map((a) => (a.preview_url ? mediaFullUrl(a.preview_url) : ''))
      .filter(Boolean);
    let grid = el.querySelector('.message-images');
    if (!urls.length) {
      grid?.remove();
      return;
    }
    if (!grid) {
      grid = document.createElement('div');
      grid.className = 'message-images';
      el.appendChild(grid);
    }
    grid.innerHTML = '';
    for (const url of urls) grid.appendChild(this._createImage(url));
  }

  cancelGeneration() {
    this.socket?.cancel();
    this.showError('Отмена запроса…', 3000);
  }

  startStreaming() {
    this.streaming = true;
    this._generationNotifyPending = true;
    void window.TaskNotifications?.unlockAudio?.();
    this._setUiActivityStage('llm_thinking');
    this.streamText = '';
    this.streamReasoningText = '';
    WebChatComposer.syncSendState(this);

    const el = document.createElement('div');
    el.className = 'chat-message assistant streaming waiting';
    el.innerHTML = `
      <div class="message-bubble"></div>
      <div class="message-images"></div>
      ${WebChatMessages.MESSAGE_STATUS_HTML}
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
    this.showProgress(null, { stage: 'llm_thinking' });
    this.scrollToBottom(true);
  }

  _renderStreamTextToBubble(text, { finalize = false } = {}) {
    if (!this.streamEl) return;
    const body = text ?? this.streamText ?? '';
    this.streamText = body;
    if (this.streamEl.dataset) {
      this.streamEl.dataset.rawContent = body;
    }
    const bubble = this.streamEl.querySelector('.message-bubble');
    const displayText = WebChatMessages.assistantDisplayText(body);
    const useMarkdown = finalize || !this.streaming;
    if (displayText && bubble) {
      if (useMarkdown) {
        bubble.innerHTML = formatMarkdown(displayText);
        bubble.querySelectorAll('img').forEach((img) => {
          const src = img.getAttribute('src');
          if (src) {
            img.dataset.url = mediaFullUrl(src);
            img.src = mediaPreviewUrl(src);
          }
        });
      } else {
        bubble.textContent = displayText;
      }
    } else if (bubble) {
      bubble.innerHTML = '';
    }
    this._syncAssistantLayoutClasses(this.streamEl);
  }

  onTextDelta(chunk) {
    if (!this._ensureStreamTarget()) return;
    this._setUiActivityStage('llm_typing');
    // Не прячем status при каждом text_delta: иначе он мигает из-за чередования
    // с progress/generation_update событиями и "дёргает" высоту пузыря.
    this.showProgress(null, { stage: 'llm_typing' });
    this.streamEl.classList.remove('waiting');
    this._renderStreamTextToBubble((this.streamText || '') + chunk);
    this._scheduleScrollToBottom();
  }

  onReasoningDelta(chunk) {
    if (!chunk || !this._ensureStreamTarget()) return;
    // Если уже началась печать видимого текста, не откатываем UI-стадию обратно
    // в "Размышляю…": reasoning может приходить вперемешку с text_delta.
    const hasVisibleText = Boolean(WebChatMessages.assistantDisplayText(this.streamText || ''));
    if (!hasVisibleText) {
      this._setUiActivityStage('llm_thinking');
    }
    this.streamReasoningText = (this.streamReasoningText || '') + chunk;
    this._renderStreamReasoning(this.streamReasoningText);
    if (this.streamEl && this.streamReasoningText.trim()) {
      this.streamEl.classList.remove('waiting');
    }
    this._scheduleScrollToBottom();
  }

  _ensureStreamReasoningShell() {
    if (!this.streamEl) return null;
    let details = this.streamEl.querySelector('.message-reasoning');
    if (!details) {
      details = document.createElement('details');
      details.className = 'message-reasoning';
      const summary = document.createElement('summary');
      summary.className = 'message-reasoning-summary';
      summary.textContent = 'Размышления модели';
      const body = document.createElement('pre');
      body.className = 'message-reasoning-body';
      details.append(summary, body);
      this.streamEl.insertBefore(details, this.streamEl.firstChild);
    }
    return details.querySelector('.message-reasoning-body');
  }

  _renderStreamReasoning(text) {
    const pre = this._ensureStreamReasoningShell();
    if (!pre) return;
    pre.textContent = text || '';
    this._syncAssistantLayoutClasses(this.streamEl);
  }

  onImages(urls) {
    const streamId = this.streamRow?.dataset?.messageId;
    if (streamId) this._ensureStreamBoundToMessageId(streamId);
    if (!this._ensureStreamTarget() || !this.streamImagesEl) return;
    const added = this._appendImagesToGrid(this.streamImagesEl, urls);
    if (added > 0) {
      this._generationHadImages = true;
      // Пока ход ещё идёт, статус оставляем (SD batch / img2img): CSS умеет текст+картинки+progress.
      if (!this._generationResumeActive && !this.streaming) {
        this.hideProgress();
      }
      this._syncAssistantLayoutClasses(this.streamEl);
    }
    this._scheduleScrollToBottom();
  }

  onToolStart(name) {
    if (name === 'generate_image' || name === 'img2img' || name === 'upscale_images') {
      this._generationHadImages = true;
    }
    this._ensureStreamTarget();
    const stage = name === 'upscale_images' ? 'sd_upscale' : (
      name === 'generate_image' || name === 'img2img' ? 'sd_render' : (
        name === 'extract_text' ? 'doc_read' : (
          name === 'get_gallery' ? 'gallery' : 'llm_tools'
        )
      )
    );
    this._setUiActivityStage(stage);
    this.showProgress(null, { stage, tool: name });
    this._scheduleScrollToBottom();
  }

  onToolDone(name, summary, skipped = false) {
    if (skipped) {
      this._ensureStreamTarget();
      const toolLabels = window.TOOL_USER_LABELS || {};
      const toolLabel = toolLabels[name] || name || 'Инструмент';
      const detail = (summary || '').trim()
        || 'Повторный вызов пропущен: лимит в этом ходе.';
      this.showProgress(toolLabel, {
        stage: name === 'upscale_images' ? 'sd_upscale' : (
          name === 'generate_image' || name === 'img2img' ? 'sd_render' : 'llm_tools'
        ),
        tool: name,
        detail,
      });
      this._scheduleScrollToBottom();
      return;
    }
    if (
      this.streamEl
      && !this.streamText
      && !this.streamImagesEl?.children.length
    ) {
      this.showProgress(null, { stage: 'llm_thinking' });
    } else if (!this.streamText) {
      this.hideProgress();
    }
  }

  onProgress(msg) {
    if (!msg) return;
    this._ensureStreamTarget();
    if (msg.stage) {
      this._setUiActivityStage(msg.stage);
    }
    this.showProgress(msg.label, {
      stage: msg.stage,
      tool: msg.tool,
      percent: msg.percent,
      detail: msg.detail,
      preview: msg.preview,
    });
    this._scheduleScrollToBottom();
  }

  onGenerationUpdate(msg) {
    if (!msg || !this.currentConvId) return;
    if (msg.in_progress) {
      this._generationResumeActive = true;
      this._ensureStreamTarget();
    }
    this._syncResumeProgress(msg);
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
      if (this.streamText) {
        this._renderStreamTextToBubble(this.streamText);
      }
      this._attachActions(tempRow, 'assistant');
      this._removeExtraStreamRows(id);
      return;
    }
    this._removeExtraStreamRows(id);
    if (!this._bindStreamToMessageId(id)) {
      void this._ensureAssistantDraftRow(id);
    }
  }

  _removeExtraStreamRows(keepMessageId) {
    if (keepMessageId) {
      WebChatMessages.dedupeMessageRows(this, { preferMessageId: keepMessageId });
    }
    this.$.chatMessages.querySelectorAll('.message-row.assistant').forEach((row) => {
      const mid = row.dataset.messageId;
      if (!mid || mid === keepMessageId) return;
      const el = row.querySelector('.chat-message.assistant');
      if (el?.classList.contains('streaming')) {
        this._settleStreamElement(el);
        row.removeAttribute('data-streaming-draft');
      }
    });
    const orphanTemp = this.$.chatMessages.querySelector(
      '.message-row.assistant[data-temp="true"]',
    );
    if (orphanTemp && orphanTemp !== this.streamRow) {
      orphanTemp.remove();
    }
  }

  async _ensureAssistantDraftRow(messageId) {
    try {
      if (this._findRow(messageId)) {
        this._bindStreamToMessageId(messageId);
        return;
      }
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=30`,
      );
      const draft = messages.find((m) => m.id === messageId);
      if (draft && !this._findRow(messageId)) {
        this.$.chatMessages.appendChild(this._buildAssistantDraftRow(draft));
      }
      this._bindStreamToMessageId(messageId);
    } catch (err) {
      this.log?.warn('chat', err.message);
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
    if (this.streamText && bubble) {
      const displayText = WebChatMessages.assistantDisplayText(this.streamText);
      bubble.innerHTML = formatMarkdown(displayText);
    }
    this._syncAssistantLayoutClasses(this.streamEl);
    WebChatComposer.syncSendState(this);
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
    const hasText = Boolean(WebChatMessages.assistantDisplayText(this.streamText || ''));
    const hasImages = Boolean(this.streamImagesEl?.children.length);
    const hasReasoning = Boolean(
      this.streamEl?.querySelector('.message-reasoning-body')?.textContent?.trim(),
    );

    if (status.progress_label || status.progress_stage) {
      this._setUiActivityStage(status.progress_stage);
      this.showProgress(status.progress_label, {
        stage: status.progress_stage,
        percent: status.progress_percent,
        detail: status.progress_detail,
        tool: status.active_tool,
      });
      return;
    }

    if (status.phase === 'tool' && status.active_tool) {
      const stage = status.active_tool === 'upscale_images' ? 'sd_upscale' : 'sd_render';
      this._setUiActivityStage(stage);
      this.showProgress(null, {
        stage,
        tool: status.active_tool,
      });
      return;
    }
    if (hasText && !hasImages && status.in_progress) {
      this._setUiActivityStage('llm_typing');
      this.showProgress(null, { stage: 'llm_typing' });
      return;
    }
    if ((hasText || hasImages) && status.in_progress) {
      const stage = status.active_tool === 'upscale_images' ? 'sd_upscale' : (
        status.active_tool === 'generate_image' || status.active_tool === 'img2img'
          ? 'sd_render'
          : (hasText ? 'llm_typing' : 'llm_thinking')
      );
      this._setUiActivityStage(stage);
      this.showProgress(null, {
        stage,
        tool: status.active_tool,
        percent: status.progress_percent,
        detail: status.progress_detail,
      });
      return;
    }
    if ((hasText || hasImages || hasReasoning) && !status.in_progress) {
      this.hideProgress();
      return;
    }
    if (hasReasoning && !hasText && !hasImages && status.in_progress) {
      this._setUiActivityStage('llm_thinking');
      this.showProgress(null, { stage: 'llm_thinking' });
      return;
    }
    this._setUiActivityStage('llm_thinking');
    this.showProgress(null, { stage: 'llm_thinking' });
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
      if (this.streaming || this._generationResumeActive || status.streaming_message_id) {
        this._generationNotifyPending = Boolean(this.streaming || this._generationResumeActive);
        await this._completeGenerationUi({ preserveScroll: !this._scrollStuckToBottom });
        if (this._generationNotifyPending) {
          this._notifyGenerationComplete({ conversationTitle: this.currentConv?.title });
        }
      } else {
        this._generationResumeActive = false;
      }
      return;
    }

    this._generationResumeActive = true;
    this._generationNotifyPending = true;
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
    if (!bound && streamId) {
      if (this._findRow(streamId)) {
        bound = this._bindStreamToMessageId(streamId);
      } else {
        const messages = await this.api(
          `/api/conversations/${this.currentConvId}/messages?limit=50`,
        );
        const draft = messages.find((m) => m.id === streamId);
        if (draft && !this._findRow(streamId)) {
          this.$.chatMessages.appendChild(this._buildAssistantDraftRow(draft));
          bound = this._bindStreamToMessageId(draft.id);
        }
      }
    }
    if (!bound) {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      if (messages[messages.length - 1]?.role === 'user') {
        this._beginResumePlaceholder();
        if (streamId && this.streamRow) {
          this.streamRow.dataset.messageId = streamId;
          this.streamRow.removeAttribute('data-temp');
        }
      }
    }

    await this._refreshStreamingBubbleFromServer(status);
    this._syncResumeProgress(status);
    if (streamId) {
      WebChatMessages.dedupeMessageRows(this, { preferMessageId: streamId });
    }
    this._watchGenerationUntilDone();
  }

  _ensureStreamTarget() {
    if (this.streamEl) return true;
    if (!this._generationResumeActive) {
      this._generationResumeActive = true;
      if (!this._generationWatchRunning) this._watchGenerationUntilDone();
    }
    this._beginResumePlaceholder();
    return Boolean(this.streamEl);
  }

  async _refreshStreamingBubbleFromServer(statusFromSync = null) {
    if (!this.currentConvId) return;
    const boundId = this.streamRow?.dataset?.messageId || null;
    if (boundId) this._ensureStreamBoundToMessageId(boundId);
    const isTemp = this.streamRow?.hasAttribute('data-temp');

    let authoritativeId = boundId;
    if (!authoritativeId) {
      const st = statusFromSync || await this.api(
        `/api/conversations/${this.currentConvId}/generation-status`,
      );
      authoritativeId = st?.streaming_message_id || null;
    }
    if (!authoritativeId) {
      if (isTemp) return;
      return;
    }

    const messages = await this.api(
      `/api/conversations/${this.currentConvId}/messages?limit=50`,
    );
    const target = messages.find((m) => m.id === authoritativeId);
    if (!target) return;

    if (!this.streamEl || boundId !== target.id) {
      if (isTemp && this.streamRow) {
        this.streamRow.dataset.messageId = target.id;
        this.streamRow.removeAttribute('data-temp');
        this._applyStreamUI(this.streamRow);
        this._attachActions(this.streamRow, 'assistant');
      } else {
        this._bindStreamToMessageId(target.id);
      }
    }
    if (!this.streamEl) return;

    const newText = target.content_text || '';
    const localText = this.streamText || '';
    const live = this.streaming || this._generationResumeActive;
    if (
      newText
      && (newText.length > localText.length || (!live && newText !== localText))
    ) {
      this._renderStreamTextToBubble(newText);
      if (!live) {
        this.hideProgress();
      }
    }

    const urls = WebChatMessages.imageUrlsFromMessage(target);
    if (urls.length && this.streamImagesEl) {
      this._syncStreamImagesFromServer(urls);
    }

    if (target.id && this.streamRow) {
      this.streamRow.dataset.messageId = target.id;
      this.streamRow.removeAttribute('data-streaming-draft');
      this._attachActions(this.streamRow, 'assistant');
    }

    this._syncAssistantLayoutClasses(this.streamEl);
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
          this._generationWatchRunning = false;
          const stick = this._scrollStuckToBottom;
          await this._completeGenerationUi({ preserveScroll: !stick });
          this._notifyGenerationComplete({ conversationTitle: this.currentConv?.title });
          this._messagesFingerprint = await this._messagesFingerprintFromServer();
          this._conversationsFingerprint = '';
          await this._syncConversationsFromServer();
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

  /** Звук и системное уведомление о завершении генерации (один раз на ход). */
  _notifyGenerationComplete({ conversationTitle } = {}) {
    if (!this._generationNotifyPending) return;
    this._generationNotifyPending = false;
    const hadImages = this._generationHadImages;
    this._generationHadImages = false;
    const title = conversationTitle || this.currentConv?.title || 'web-chat';
    const body = hadImages
      ? 'Генерация изображений завершена'
      : 'Ответ ассистента готов';
    void window.TaskNotifications?.notifyTaskDone({
      title,
      body,
      tag: `webchat-done-${this.currentConvId || 'chat'}`,
    });
  }

  onTurnDone(msg) {
    const assistantMessageId = msg?.assistant_message_id;
    const conversationTitle = msg?.conversation_title;
    WebChatPreloadModels?.recordWarmedAt?.();
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.hideProgress();
    this._notifyGenerationComplete({ conversationTitle });
    const hadRegenerate = this._regenerating;
    this._regenerating = false;

    const afterReload = () => {
      void (async () => {
        if (assistantMessageId) {
          await this._syncAssistantImagesToDom(assistantMessageId);
        }
        await this._attachRagSourcesAfterTurn(assistantMessageId);
        this.endStreaming();
        if (conversationTitle && this.currentConv) {
          this.currentConv.title = conversationTitle;
          const conv = this.conversations.find((c) => c.id === this.currentConvId);
          if (conv) conv.title = conversationTitle;
          this._setSettingsChatTitle(conversationTitle);
        }
        this._conversationsFingerprint = '';
        void this.loadConversations();
      })();
    };

    const hasLiveStream = Boolean(
      this.streamRow && (this.streamText || this.streamImagesEl?.children.length),
    );

    if (assistantMessageId && hasLiveStream && !hadRegenerate) {
      void (async () => {
        await this._syncFinalAssistantText(assistantMessageId);
        this._finalizeStreamRow(assistantMessageId);
        this._settleAllStreamRows();
        try {
          this._messagesFingerprint = await this._messagesFingerprintFromServer();
        } catch {
          /* fingerprint optional */
        }
        afterReload();
      })();
      return;
    }

    if (this.currentConvId) {
      void this._completeGenerationUi({ preserveScroll: !this._scrollStuckToBottom })
        .then(afterReload)
        .catch(() => afterReload());
    } else {
      afterReload();
    }
  }

  /**
   * Завершение генерации в UI: финализация черновика или перезагрузка без дублей.
   */
  async _syncFinalAssistantText(messageId) {
    if (!this.currentConvId || !messageId) return;
    this._ensureStreamBoundToMessageId(messageId);
    try {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const target = messages.find((m) => m.id === messageId);
      if (!target) return;
      const serverText = target.content_text || '';
      if (serverText.length >= (this.streamText || '').length) {
        this._renderStreamTextToBubble(serverText, { finalize: true });
      }
      const urls = WebChatMessages.imageUrlsFromMessage(target);
      if (urls.length) {
        if (this.streamImagesEl) {
          this._syncStreamImagesFromServer(urls);
        } else {
          await this._syncAssistantImagesToDom(messageId);
        }
      }
      const ragHits = target.content_json?.rag_sources;
      if (ragHits?.length) {
        const row = this._findRow(messageId);
        const msgEl = row?.querySelector('.chat-message.assistant');
        if (msgEl && !msgEl.querySelector('.message-rag-sources')) {
          const block = WebChatMessageBlocks.buildMessageRagSources(ragHits);
          if (block) msgEl.appendChild(block);
        }
      }
      const reasoning = target.content_json?.reasoning;
      if (reasoning?.trim()) {
        const row = this._findRow(messageId);
        const msgEl = row?.querySelector('.chat-message.assistant');
        if (msgEl && !msgEl.querySelector('.message-reasoning')) {
          const block = WebChatMessageBlocks.buildMessageReasoning(reasoning);
          if (block) msgEl.insertBefore(block, msgEl.firstChild);
        }
      }
    } catch (err) {
      this.log?.warn('chat', `sync final text: ${err.message}`);
    }
  }

  async _completeGenerationUi({ preserveScroll = false } = {}) {
    this._generationResumeActive = false;
    const messageId = this.streamRow?.dataset?.messageId;
    const hasLiveStream = Boolean(
      this.streamRow && (this.streamText || this.streamImagesEl?.children.length),
    );
    if (hasLiveStream && messageId) {
      await this._syncFinalAssistantText(messageId);
      this._finalizeStreamRow(messageId);
    } else {
      await this.loadMessages({ preserveScroll });
    }
    if (messageId) {
      await this._syncAssistantImagesToDom(messageId);
    }
    this._settleAllStreamRows();
    this.endStreaming();
  }

  _settleStreamElement(el) {
    if (!el) return;
    if (el === this.streamEl && this.streamText) {
      this._renderStreamTextToBubble(this.streamText, { finalize: true });
    }
    el.classList.remove('streaming', 'waiting', 'is-busy');
    const status = el.querySelector('.message-status');
    status?.classList.add('hidden');
    this._syncAssistantLayoutClasses(el);
  }

  _settleAllStreamRows() {
    this.$.chatMessages?.querySelectorAll('.chat-message.assistant.streaming').forEach((el) => {
      this._settleStreamElement(el);
    });
    this.$.chatMessages?.querySelectorAll('.message-row[data-streaming-draft="true"]').forEach(
      (row) => {
        row.removeAttribute('data-streaming-draft');
      },
    );
    if (!this.streaming) {
      this.$.chatMessages?.querySelectorAll('.message-row.assistant[data-temp="true"]').forEach(
        (row) => row.remove(),
      );
    }
  }

  _finalizeStreamRow(messageId) {
    if (!this.streamRow) return;
    this.streamRow.dataset.messageId = messageId;
    this.streamRow.removeAttribute('data-streaming-draft');
    this.streamRow.removeAttribute('data-temp');
    if (this.streamEl) {
      this._settleStreamElement(this.streamEl);
    }
    this._attachActions(this.streamRow, 'assistant');
    if (this._scrollStuckToBottom) {
      this._scheduleScrollToBottom(true);
    }
  }

  onWsError(message, code, errorId) {
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this._generationNotifyPending = false;
    this._generationHadImages = false;
    this.hideProgress();
    this.log?.error('ws', `Ошибка генерации (${code || 'unknown'})`, {
      message,
      code: code || null,
      error_id: errorId || null,
      conversation_id: this.currentConvId || null,
    });
    if (code === 'tool_loop' && this.currentConvId) {
      this.loadMessages().catch(() => {});
    }
    const opts = errorId ? { errorId } : {};
    if (code !== 'cancelled') {
      this.showError(message || 'Ошибка генерации', 8000, opts);
    } else {
      this.log?.info('ws', message || 'Генерация отменена');
      this.showSuccess(message || 'Генерация отменена', 4000);
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
    this._uiActivityStage = null;
    this._recomputeUiState();
    WebChatComposer.syncSendState(this);
    if (!this.$.userInput.disabled) {
      this.$.userInput.focus();
    }

    if (this.streamEl) {
      this._settleStreamElement(this.streamEl);
      if (!this.streamText && !this.streamImagesEl?.children.length) {
        this.streamRow?.remove();
      }
      this.streamEl = null;
      this.streamRow = null;
      this.streamImagesEl = null;
      this.streamReasoningText = '';
    }
    this._settleAllStreamRows();
    this._syncAllMessageActions();
  }

  _getScrollPositionEntry(convId) {
    return WebChatScroll.getEntry(convId);
  }

  _saveScrollPosition(convId = this.currentConvId) {
    WebChatScroll.save(this, convId);
  }

  _clearScrollPosition(convId) {
    WebChatScroll.clear(convId);
  }

  _scheduleScrollPositionSave() {
    WebChatScroll.scheduleSave(this);
  }

  _applyScrollRestore(entry) {
    WebChatScroll.applyRestore(this, entry);
  }

  _restoreScrollPosition(convId) {
    WebChatScroll.restore(this, convId);
  }


  _setSettingsChatTitle(title) {
    WebChatSettings.setSettingsChatTitle(this, title);
  }

  _settingsChatTitleDraft() {
    return WebChatSettings.settingsChatTitleDraft(this);
  }

  async saveSettings() {
    return WebChatSettings.save(this);
  }

  _hideSettingsSaveStatus() {
    WebChatSettings.hideSaveStatus(this);
  }

  _showSettingsSaveStatus(kind, message) {
    WebChatSettings.showSaveStatus(this, kind, message);
  }

  _loadModelSettings() {
    WebChatSettings.loadModelSettings(this);
  }

  _normalizeServiceUrl(raw, opts) {
    return WebChatSettings.normalizeServiceUrl(this, raw, opts);
  }

  _loadIntegrationUrlFields() {
    WebChatSettings.loadIntegrationUrlFields(this);
  }

  _saveIntegrationUrls() {
    WebChatSettings.saveIntegrationUrls(this);
  }

  _updateTrustedInternalHint() {
    WebChatSettings.updateTrustedInternalHint(this);
  }

  async _syncTrustedInternalHosts(llmUrl, sdUrl) {
    return WebChatSettings.syncTrustedInternalHosts(this, llmUrl, sdUrl);
  }

  async loadLlmModelInfo() {
    return WebChatSettings.loadLlmModelInfo(this);
  }

  async loadSdModelInfo() {
    return WebChatSettings.loadSdModelInfo(this);
  }

  _syncSdModelSelectState() {
    WebChatSettings.syncSdModelSelectState(this);
  }

  async applySdModelSelection(opts = {}) {
    return WebChatSettings.applySdModelSelection(this, opts);
  }

  _syncModelInputState() {
    WebChatSettings.syncModelInputState(this);
  }

  _saveModelOverride() {
    WebChatSettings.saveModelOverride(this);
  }

  getActiveLlmModel() {
    return WebChatSettings.getActiveLlmModel(this);
  }

  showError(msg, autoHideMs = 8000, options = {}) {
    let text = msg;
    if (options.errorId) {
      text = `${msg} Код для поддержки: ${options.errorId}`;
    }
    this.log?.error('ui', text);
    clearTimeout(this._errorTimer);
    this.$.errorBannerText.textContent = text;
    this.$.errorBannerRetry?.classList.toggle('hidden', !options.showRetry);
    this.$.errorBanner.classList.remove('hidden', 'is-success');
    if (autoHideMs > 0) {
      this._errorTimer = setTimeout(() => this.hideError(), autoHideMs);
    }
  }

  showSuccess(msg, autoHideMs = 4000) {
    this.log?.info('ui', msg);
    clearTimeout(this._errorTimer);
    this.$.errorBannerText.textContent = msg;
    this.$.errorBannerRetry?.classList.add('hidden');
    this.$.errorBanner.classList.add('is-success');
    this.$.errorBanner.classList.remove('hidden');
    if (autoHideMs > 0) {
      this._errorTimer = setTimeout(() => this.hideError(), autoHideMs);
    }
  }

  hideError() {
    this.$.errorBanner.classList.add('hidden');
    this.$.errorBanner.classList.remove('is-success');
    this.$.errorBannerRetry?.classList.add('hidden');
    clearTimeout(this._errorTimer);
  }

  showUploadSuccess(message) {
    const el = this.$.uploadToast;
    if (!el) return;
    clearTimeout(this._uploadToastTimer);
    el.textContent = message;
    el.classList.remove('hidden');
    requestAnimationFrame(() => el.classList.add('is-visible'));
    this._uploadToastTimer = setTimeout(() => {
      el.classList.remove('is-visible');
      setTimeout(() => el.classList.add('hidden'), 220);
    }, 2800);
  }

  _retrySocketConnection() {
    if (!this.currentConvId) return;
    this.hideError();
    this._wsOfflineBannerShown = false;
    this._wsReconnecting = false;
    this.log?.info('ws', 'Ручной перезапуск подключения');
    this.connectSocket();
  }

  _ensureStatusPreviewNodes(status) {
    let wrap = status.querySelector('.message-status-preview-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'message-status-preview-wrap hidden';
      wrap.setAttribute('aria-hidden', 'true');
      const img = document.createElement('img');
      img.className = 'message-status-preview';
      img.alt = '';
      img.decoding = 'async';
      wrap.appendChild(img);
      const pill = status.querySelector('.message-status-pill');
      if (pill) status.insertBefore(wrap, pill);
      else status.prepend(wrap);
    }
    return {
      wrap,
      img: wrap.querySelector('.message-status-preview'),
    };
  }

  _setStatusPreview(status, previewUrl) {
    if (!status) return;
    const { wrap, img } = this._ensureStatusPreviewNodes(status);
    const msgEl = status.closest('.chat-message');
    const url = (previewUrl || '').trim();
    if (!url || !img) {
      wrap.classList.add('hidden');
      wrap.setAttribute('aria-hidden', 'true');
      img?.removeAttribute('src');
      status.classList.remove('has-preview');
      msgEl?.classList.remove('has-live-preview');
      return;
    }
    if (img.getAttribute('src') !== url) {
      img.src = url;
    }
    img.alt = 'Превью генерации';
    wrap.classList.remove('hidden');
    wrap.setAttribute('aria-hidden', 'false');
    status.classList.add('has-preview');
    msgEl?.classList.add('has-live-preview');
  }

  showProgress(text, opts = {}) {
    if (!this.streamEl) return;
    const status = this.streamEl.querySelector('.message-status');
    const labelEl = this.streamEl.querySelector('.message-status-text');
    const detailEl = this.streamEl.querySelector('.message-status-detail');
    const percentEl = this.streamEl.querySelector('.message-status-percent');
    if (!status || !labelEl) return;

    const resolvedLabel = this._resolveProgressLabel(text, opts);
    labelEl.textContent = resolvedLabel;

    const detail = (opts.detail || '').trim();
    if (detailEl) {
      detailEl.textContent = detail;
      detailEl.classList.toggle('hidden', !detail);
    }

    const hasPercent = typeof opts.percent === 'number' && !Number.isNaN(opts.percent);
    if (percentEl) {
      if (hasPercent) {
        percentEl.textContent = `${Math.round(opts.percent)}%`;
        percentEl.classList.remove('hidden');
      } else {
        percentEl.textContent = '';
        percentEl.classList.add('hidden');
      }
    }

    if (opts.preview) {
      this._setStatusPreview(status, opts.preview);
    }

    status.classList.remove('hidden');
    this.streamEl.classList.toggle('has-live-preview', status.classList.contains('has-preview'));
    this.streamEl.classList.add('waiting', 'is-busy');
    if (opts.stage) {
      status.dataset.stage = opts.stage;
    }
  }

  hideProgress() {
    if (!this.streamEl) return;
    const status = this.streamEl.querySelector('.message-status');
    if (status) this._setStatusPreview(status, null);
    this.streamEl?.classList.remove('has-live-preview');
    status?.classList.add('hidden');
    this.streamEl.classList.remove('is-busy');
    const hasBody = this.streamEl.classList.contains('has-content')
      || this.streamEl.classList.contains('has-images')
      || this.streamEl.classList.contains('has-reasoning');
    if (hasBody) {
      this.streamEl.classList.remove('waiting');
    }
  }

  _distanceFromBottom(el) {
    const scrollEl = el ?? WebChatScroll.chatHistoryScrollEl(this);
    return WebChatScroll.distanceFromBottom(scrollEl);
  }

  _onChatScroll() {
    const dist = this._distanceFromBottom();
    if (dist <= SCROLL_STICKY_PX) {
      this._scrollStuckToBottom = true;
    } else {
      this._scrollStuckToBottom = false;
    }
    this._updateScrollBtn();
    this._scheduleScrollPositionSave();
  }

  _scheduleScrollToBottom(force = false) {
    if (this._scrollRaf != null) return;
    this._scrollRaf = requestAnimationFrame(() => {
      this._scrollRaf = null;
      this.scrollToBottom(force);
    });
  }

  scrollToBottom(force = false) {
    const el = WebChatScroll.chatHistoryScrollEl(this);
    if (!el) return;
    const dist = this._distanceFromBottom(el);
    if (force) {
      this._scrollStuckToBottom = true;
      el.scrollTop = el.scrollHeight;
    } else if (dist <= SCROLL_FOLLOW_PX) {
      this._scrollStuckToBottom = true;
      el.scrollTop = el.scrollHeight;
    } else {
      this._scrollStuckToBottom = false;
    }
    this._updateScrollBtn();
    if (force && this.currentConvId) {
      this._saveScrollPosition(this.currentConvId);
    }
  }

  _updateScrollBtn() {
    const el = WebChatScroll.chatHistoryScrollEl(this);
    const dist = this._distanceFromBottom(el);
    const show = dist > SCROLL_STICKY_PX;
    this.$.scrollBtn.classList.toggle('visible', show);
    if (show) {
      this.$.scrollBtn.title = this._scrollStuckToBottom
        ? 'Вниз'
        : 'Вниз (следовать за новыми сообщениями)';
    }
  }

  async attachImageUrlToComposer(url, btn = null, { closeLightbox = false } = {}) {
    const resolved = mediaFullUrl(url);
    if (!resolved) return;
    if (!this.currentConvId) {
      this.showError('Сначала выберите или создайте беседу');
      return;
    }
    const key = WebChatMessages.imageUrlKey(resolved);
    if (this.pendingAttachments.some((a) => WebChatMessages.imageUrlKey(mediaFullUrl(a.preview_url)) === key)) {
      this.showError('Это изображение уже прикреплено', 3000);
      return;
    }
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(resolved);
      if (!res.ok) throw new Error('Не удалось загрузить изображение');
      const blob = await res.blob();
      const mime = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
      const file = new File([blob], WebChatLightbox.filenameFromUrl(resolved), { type: mime });
      await WebChatComposer.uploadFiles(this, [file]);
      if (closeLightbox) WebChatLightbox.close(this);
      this.$.userInput?.focus();
    } catch (err) {
      this.showError(err.message || 'Ошибка прикрепления');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  openLightbox(url) {
    WebChatLightbox.open(this, url);
  }

  closeLightbox() {
    WebChatLightbox.close(this);
  }

  _favoriteTitle(isFavorite) {
    return isFavorite ? 'Убрать из избранного' : 'В избранное';
  }

  _setFavoriteButtonState(btn, isFavorite) {
    if (!btn) return;
    btn.classList.toggle('is-favorite', Boolean(isFavorite));
    const title = this._favoriteTitle(Boolean(isFavorite));
    btn.title = title;
    btn.setAttribute('aria-label', title);
  }

  async _syncFavoriteVisualByUrl(url, btn) {
    const target = parseMediaGalleryTarget(url);
    if (!target || !btn) return;
    const key = `${target.source}:${target.id}`;
    if (this._favoriteStateCache.has(key)) {
      this._setFavoriteButtonState(btn, this._favoriteStateCache.get(key));
      return;
    }
    try {
      const res = await fetch(`/api/gallery/favorite/state?source=${encodeURIComponent(target.source)}&id=${encodeURIComponent(target.id)}`);
      if (!res.ok) return;
      const data = await res.json();
      if (this._favoriteStateCache.has(key)) {
        this._setFavoriteButtonState(btn, this._favoriteStateCache.get(key));
        return;
      }
      const favored = Boolean(data?.is_favorite);
      this._favoriteStateCache.set(key, favored);
      this._setFavoriteButtonState(btn, favored);
    } catch {
      /* ignore */
    }
  }

  async _promoteToUploadsByUrl(url, btn) {
    const full = mediaFullUrl(url);
    if (!full) {
      this.showError('Нет адреса изображения');
      return;
    }
    const prevDisabled = btn?.disabled;
    if (btn) btn.disabled = true;
    try {
      await promoteMediaToUploads(full);
      this.showSuccess('Добавлено в галерею загрузок');
    } catch (err) {
      this.showError(err.message || 'Не удалось добавить в галерею загрузок');
    } finally {
      if (btn) btn.disabled = prevDisabled ?? false;
    }
  }

  async _toggleFavoriteByUrl(url, btn) {
    const target = parseMediaGalleryTarget(url);
    if (!target || !btn) return;
    const wasFavorite = btn.classList.contains('is-favorite');
    const next = !wasFavorite;
    const cacheKey = `${target.source}:${target.id}`;
    const applyAll = (state) => {
      this._favoriteStateCache.set(cacheKey, state);
      this.$.chatMessages
        ?.querySelectorAll(`.message-image-favorite[data-media-key="${CSS.escape(cacheKey)}"]`)
        .forEach((node) => this._setFavoriteButtonState(node, state));
      this._setFavoriteButtonState(this.$.lightboxFavorite, state);
    };
    applyAll(next);
    try {
      const res = await fetch('/api/gallery/favorite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: target.source,
          id: target.id,
          favorite: next,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      const payload = await res.json();
      const confirmed = Boolean(payload.is_favorite);
      if (confirmed !== next) applyAll(confirmed);
    } catch (err) {
      applyAll(wasFavorite);
      this.showError(err.message || 'Не удалось обновить избранное');
    }
  }

  _loadTheme() {
    WebChatAppearance.loadTheme();
  }

  _updateThemeToggleLabel() {
    WebChatAppearance.updateThemeToggleLabel(this);
  }

  getMacroContextMode() {
    let mode = sessionStorage.getItem(MACRO_CONTEXT_MODE_KEY);
    if (!mode && sessionStorage.getItem(MACRO_CONTEXT_FULL_LEGACY) === '1') {
      mode = 'full';
      sessionStorage.setItem(MACRO_CONTEXT_MODE_KEY, mode);
      sessionStorage.removeItem(MACRO_CONTEXT_FULL_LEGACY);
    }
    return mode === 'full' || mode === 'semantic' ? mode : 'selected';
  }

  _cycleMacroContextMode() {
    const order = ['selected', 'full', 'semantic'];
    const cur = this.getMacroContextMode();
    const idx = order.indexOf(cur);
    return order[(idx + 1) % order.length];
  }


  _initMacroContextToggle() {
    const btn = this.$.macroContextFullBtn;
    if (!btn) return;
    this._updateMacroContextToggleUi();
    btn.addEventListener('click', () => {
      const next = this._cycleMacroContextMode();
      if (next === 'selected') {
        sessionStorage.removeItem(MACRO_CONTEXT_MODE_KEY);
      } else {
        sessionStorage.setItem(MACRO_CONTEXT_MODE_KEY, next);
      }
      this._updateMacroContextToggleUi();
      const labels = {
        selected: 'Только @alias из текста',
        full: 'Полный каталог @alias в контексте модели',
        semantic: 'Top-K @alias по смыслу запроса (semantic)',
      };
      this.log?.info('macro', labels[next] || next);
    });
  }

  _updateMacroContextToggleUi() {
    const btn = this.$.macroContextFullBtn;
    if (!btn) return;
    const mode = this.getMacroContextMode();
    btn.classList.toggle('active', mode !== 'selected');
    btn.classList.toggle('semantic', mode === 'semantic');
    btn.setAttribute('aria-pressed', mode !== 'selected' ? 'true' : 'false');
    const labels = {
      selected: 'Каталог @alias',
      full: 'Каталог @alias · полный',
      semantic: 'Каталог @alias · semantic',
    };
    const labelEl = btn.querySelector('.composer-tools-menu-label');
    if (labelEl) labelEl.textContent = labels[mode] || labels.selected;
    const titles = {
      selected: 'Контекст @alias: только из текста (нажмите — полный каталог)',
      full: 'Контекст @alias: полный каталог (нажмите — semantic top-K)',
      semantic: 'Контекст @alias: semantic top-K (нажмите — выкл.)',
    };
    btn.title = titles[mode] || titles.selected;
  }

  getWsIntegrationPayload() {
    const payload = {};
    const llmUrl = WebChatSettings.normalizeServiceUrl(this, this.$.llmBaseUrlInput?.value);
    const sdUrl = WebChatSettings.normalizeServiceUrl(this, this.$.sdWebuiUrlInput?.value);
    if (llmUrl) payload.llm_base_url = llmUrl;
    if (sdUrl) payload.sd_webui_url = sdUrl;
    const model = this.getActiveLlmModel();
    if (model) payload.model = model;
    const macroMode = this.getMacroContextMode();
    if (macroMode !== 'selected') payload.macro_context = macroMode;
    if (this.getDocumentRagEnabled()) payload.document_rag = true;
    if (this.config?.wd_tagger_enabled) {
      payload.wd_tagger = WebChatSettings.isWdTaggerEnabled(this);
    }
    return payload;
  }

  getDocumentRagEnabled() {
    if (!this.config.rag_enabled) return false;
    return sessionStorage.getItem(DOCUMENT_RAG_KEY) === '1';
  }

  _initDocumentRagToggle() {
    const btn = this.$.documentRagBtn;
    if (!btn) return;
    this._updateDocumentRagToggleUi();
    btn.addEventListener('click', () => {
      const next = !this.getDocumentRagEnabled();
      if (next) {
        sessionStorage.setItem(DOCUMENT_RAG_KEY, '1');
      } else {
        sessionStorage.removeItem(DOCUMENT_RAG_KEY);
        this._hideRagPreview();
      }
      this._updateDocumentRagToggleUi();
      this._scheduleRagPreview();
      this.log?.info('rag', next ? 'Контекст документов включён' : 'Контекст документов выключен');
    });
  }

  _updateDocumentRagToggleUi() {
    const btn = this.$.documentRagBtn;
    if (!btn) return;
    const on = this.getDocumentRagEnabled();
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    const labelEl = btn.querySelector('.composer-tools-menu-label');
    if (labelEl) {
      labelEl.textContent = on ? 'Поиск по документам · вкл' : 'Поиск по документам';
    }
    btn.title = on
      ? 'RAG по документам: вкл (фрагменты в контексте модели)'
      : 'RAG по документам: выкл (нажмите для поиска по PDF/DOCX беседы)';
  }

  _scheduleRagPreview() {
    if (!this.getDocumentRagEnabled() || !this.currentConvId) {
      this._hideRagPreview();
      return;
    }
    const q = (this.$.userInput?.value || '').trim();
    if (q.length < 3) {
      const el = this.$.documentRagPreview;
      if (!el) return;
      el.classList.remove('hidden');
      el.replaceChildren();
      const title = document.createElement('p');
      title.className = 'document-rag-preview-title';
      title.textContent = 'Поиск по документам';
      const hint = document.createElement('p');
      hint.className = 'document-rag-preview-empty';
      hint.textContent = 'Введите не менее 3 символов — покажем фрагменты из PDF и DOCX беседы';
      el.append(title, hint);
      return;
    }
    clearTimeout(this._ragPreviewTimer);
    this._ragPreviewTimer = setTimeout(() => {
      void this._fetchRagPreview(q);
    }, 400);
  }

  _hideRagPreview() {
    const el = this.$.documentRagPreview;
    if (!el) return;
    el.classList.add('hidden');
    el.replaceChildren();
  }

  async _fetchRagPreview(query) {
    const el = this.$.documentRagPreview;
    if (!el || !this.currentConvId) return;
    const seq = ++this._ragPreviewSeq;
    try {
      const hits = await this.api(
        `/api/conversations/${this.currentConvId}/document-search?q=${encodeURIComponent(query)}&limit=3`,
      );
      if (seq !== this._ragPreviewSeq) return;
      this._renderRagPreview(hits, query);
    } catch (err) {
      if (seq !== this._ragPreviewSeq) return;
      el.classList.remove('hidden');
      el.replaceChildren();
      const msg = document.createElement('p');
      msg.className = 'document-rag-preview-empty';
      msg.textContent = typeof err?.message === 'string' ? err.message : 'Поиск недоступен';
      el.appendChild(msg);
    }
  }

  _renderRagPreview(hits, query) {
    const el = this.$.documentRagPreview;
    if (!el) return;
    el.classList.remove('hidden');
    el.replaceChildren();
    const title = document.createElement('p');
    title.className = 'document-rag-preview-title';
    title.textContent = 'Фрагменты документов';
    el.appendChild(title);
    if (!hits?.length) {
      const empty = document.createElement('p');
      empty.className = 'document-rag-preview-empty';
      empty.textContent = `Нет фрагментов для «${query.slice(0, 40)}». Прикрепите PDF или DOCX к сообщению.`;
      el.appendChild(empty);
      return;
    }
    for (const hit of hits) {
      const item = document.createElement('div');
      item.className = 'document-rag-preview-item';
      const file = document.createElement('div');
      file.className = 'document-rag-preview-file';
      file.textContent = hit.file_name || 'Документ';
      const snippet = document.createElement('div');
      snippet.className = 'document-rag-preview-snippet';
      snippet.textContent = hit.snippet || '';
      item.append(file, snippet);
      el.appendChild(item);
    }
  }

  _loadFontSize() {
    WebChatAppearance.loadFontSize(this);
  }

  applyFontSize() {
    WebChatAppearance.applyFontSize(this);
  }

  changeFontSize(delta) {
    WebChatAppearance.changeFontSize(this, delta);
  }

  toggleTheme() {
    WebChatAppearance.toggleTheme(this);
  }

  async openLogsPanel() {
    return WebChatLogs.openPanel(this);
  }

  closeLogsPanel() {
    WebChatLogs.closePanel(this);
  }

  _stopLogsLiveUpdate() {
    WebChatLogs.stopLiveUpdate(this);
  }

  async _fetchServerLogs() {
    return WebChatLogs.fetchServerLogs(this);
  }

  _renderLogsView() {
    WebChatLogs.renderView(this);
  }

  async copyAllLogs() {
    return WebChatLogs.copyAll(this);
  }

  async clearAllLogs() {
    return WebChatLogs.clearAll(this);
  }

  escape(s) {
    return window.escapeHtml(s);
  }

  escapeAttr(s) {
    return window.escapeAttr(s);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  window.chatApp = new ChatApp();
});
