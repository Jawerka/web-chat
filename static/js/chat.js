/**
 * web-chat UI — REST + WebSocket
 */
/* global formatMarkdown */

/** Порог для кнопки «вниз» и ручного «прилипания». */
const SCROLL_STICKY_PX = 72;
/** Автоскролл при стриминге только если пользователь почти у низа. */
const SCROLL_FOLLOW_PX = 28;

const PRESET_DRAFTS_STORAGE_KEY = 'webchat_preset_drafts_v1';
/** Позиция прокрутки истории по беседе */
const SCROLL_POSITIONS_STORAGE_KEY = 'webchat_scroll_positions_v1';
const SCROLL_POSITION_SAVE_DEBOUNCE_MS = 400;
const SCROLL_POSITIONS_MAX_ENTRIES = 80;
/** Задержка перед оверлеем при смене беседы (быстрые переключения без мигания). */
const CONV_SWITCH_OVERLAY_DELAY_MS = 140;
const PRESET_LAST_EDIT_STORAGE_KEY = 'webchat_preset_last_edit_id';

/** Короткие подписи в плавающем селекте пресета чата */
const CHAT_PRESET_SHORT_LABELS = {
  default: 'Default',
  image_gen: 'txt2img',
  img2img: 'img2img',
  document_analysis: 'Docs',
};

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

const MSG_IMAGE_ICON_ATTACH =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
const MSG_IMAGE_ICON_SAVE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const MSG_IMAGE_ICON_DELETE =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
const MSG_IMAGE_ICON_STAR =
  '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="12 2 15.1 8.5 22 9.3 17 14.1 18.3 21 12 17.5 5.7 21 7 14.1 2 9.3 8.9 8.5 12 2"/></svg>';

const MESSAGE_STATUS_HTML = `
  <div class="message-status" role="status" aria-live="polite">
    <div class="message-status-pill">
      <span class="message-status-dots" aria-hidden="true">
        <span></span><span></span><span></span>
      </span>
      <div class="message-status-copy">
        <span class="message-status-text"></span>
        <span class="message-status-detail"></span>
      </div>
      <span class="message-status-percent" aria-hidden="true"></span>
    </div>
  </div>`;

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

/** Служебная пометка LLM о картинках — не показывать в пузыре (модель иногда копирует из контекста). */
function stripLlmImageContextNote(text) {
  if (!text) return '';
  return String(text)
    .replace(
      /\n*\[(?:CTX generated_images:[^\]]*|В этом ответе были изображения \(для контекста\):[^\]]*)\]\s*/gi,
      '',
    )
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** Текст ассистента для отображения в UI. */
function assistantDisplayText(text) {
  return stripLlmImageContextNote(stripMarkdownImages(text || ''));
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
  return [...new Set(merged.map(mediaFullUrl).filter(Boolean))];
}

/** Ключ для сравнения URL картинок (pathname, без origin). */
function imageUrlKey(url) {
  const resolved = mediaFullUrl(url);
  if (!resolved) return '';
  try {
    return new URL(resolved, window.location.origin).pathname;
  } catch {
    return resolved;
  }
}

/** sessionStorage: selected | full | semantic (Ф1/Ф2) */
const MACRO_CONTEXT_MODE_KEY = 'webchat_macro_context_mode';
const MACRO_CONTEXT_FULL_LEGACY = 'webchat_macro_context_full';
const DOCUMENT_RAG_KEY = 'webchat_document_rag_enabled';

const MSG_ICONS = {
  copy: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
  edit: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  regen: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
  delete: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
};

/** Действия над сообщением (pin/quote/retry — отложены, см. BACKLOG.md фаза 3). */
const MESSAGE_ACTION_DEFS = [
  { key: 'copy', label: 'Копировать', title: 'Скопировать текст', icon: MSG_ICONS.copy },
  { key: 'edit', label: 'Редактировать', title: 'Редактировать', icon: MSG_ICONS.edit },
  { key: 'regenerate', label: 'Перегенерировать', title: 'Перегенерировать', icon: MSG_ICONS.regen },
  { key: 'delete', label: 'Удалить', title: 'Удалить', icon: MSG_ICONS.delete, danger: true },
];

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
      documentRagPreview: document.getElementById('document-rag-preview'),
      macroInsertMenuBtn: document.getElementById('macro-insert-menu-btn'),
      settingsChatTitle: document.getElementById('settings-chat-title'),
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
      newConvModal: document.getElementById('new-conv-modal'),
      newConvPreset: document.getElementById('new-conv-preset'),
      lightbox: document.getElementById('lightbox'),
      lightboxStage: document.getElementById('lightbox-stage'),
      lightboxImg: document.getElementById('lightbox-img'),
      lightboxLoader: document.getElementById('lightbox-loader'),
      lightboxPrev: document.getElementById('lightbox-prev'),
      lightboxNext: document.getElementById('lightbox-next'),
      lightboxCounter: document.getElementById('lightbox-counter'),
      lightboxSave: document.getElementById('lightbox-save'),
      lightboxFavorite: document.getElementById('lightbox-favorite'),
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
    const saved = localStorage.getItem('webchat_conv_id');
    if (saved && this.conversations.some((c) => c.id === saved)) {
      await this.selectConversation(saved);
    }
    this._startGlobalSync();
  }

  _bindEvents() {
    document.getElementById('btn-new-chat').addEventListener('click', () => this.openNewConvModal());
    this.$.convTrashTabBtn?.addEventListener('click', () => this._toggleTrashPanel());
    this.$.convTrashEmptyAll?.addEventListener('click', () => this._onEmptyTrashClick());
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
    this.$.themeToggle?.addEventListener('click', () => this.toggleTheme());
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
    this.$.fontSizeDecrease?.addEventListener('click', () => this.changeFontSize(-1));
    this.$.fontSizeIncrease?.addEventListener('click', () => this.changeFontSize(1));
    this.$.fontSizeInput?.addEventListener('change', () => this.applyFontSize());
    document.getElementById('logs-copy-all')?.addEventListener('click', () => this.copyAllLogs());
    document.getElementById('logs-clear-all')?.addEventListener('click', () => this.clearAllLogs());
    document.getElementById('error-banner-close').addEventListener('click', () => this.hideError());
    this.$.errorBannerRetry?.addEventListener('click', () => this._retrySocketConnection());
    this.$.cancelBtn?.addEventListener('click', () => this.cancelGeneration());
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
    this._chatHistoryScrollEl()?.addEventListener('scroll', () => this._onChatScroll());
    this.$.scrollBtn.addEventListener('click', () => this.scrollToBottom(true));
    this._bindMessageImageActions();

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
    this.$.lightboxFavorite?.addEventListener('click', (e) => {
      e.stopPropagation();
      const url = this._lightboxCurrentUrl();
      if (url) void this._toggleFavoriteByUrl(url, this.$.lightboxFavorite);
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
    this.$.lightboxStage?.addEventListener('click', (e) => {
      // Закрытие по клику “мимо картинки”, но внутри сцены.
      // (По самой картинке клик не должен закрывать.)
      if (e.target === this.$.lightboxStage) this.closeLightbox();
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
          this._cancelMessageEdit();
          return;
        }
        this.closeLightbox();
        this.closeSidebar();
        if (this.$.newConvModal.open) this.$.newConvModal.close();
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
    if (this.$.newConvModal?.open) return true;
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

  _userMessageDisplayText(m) {
    const raw = m?.content_text || '';
    if (typeof WebChatImg2imgPreset !== 'undefined') {
      return WebChatImg2imgPreset.stripFromStoredMessage(raw) || raw;
    }
    return raw;
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
    WebChatImg2imgPreset?.refreshPresetCache?.(this);
    WebChatImg2imgPreset?.logDiagnostics?.(this, 'presets_loaded');
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
    const activeKey = activeId != null ? String(activeId).trim().toLowerCase() : '';
    const optionAttrs = (p) => {
      const selected = String(p.id).trim().toLowerCase() === activeKey;
      return `value="${p.id}"${selected ? ' selected' : ''}`;
    };
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
      if (activeId != null) {
        this.$.chatPresetSelect.value = String(activeId);
      }
    }
    if (this.$.convPresetSelect && activeId != null) {
      this.$.convPresetSelect.value = String(activeId);
    }
    this._updateChatPresetToolbar();
  }

  _updateChatPresetToolbar() {
    const show = Boolean(this.currentConvId) && !this.$.chatHistory?.classList.contains('hidden');
    this.$.chatPresetToolbar?.classList.toggle('hidden', !show);
    WebChatImg2imgPreset?.syncVisibility?.(this);
  }

  async onChatPresetChange() {
    const presetId = this.$.chatPresetSelect?.value;
    if (!presetId || !this.currentConvId) return;
    if (this.$.convPresetSelect) this.$.convPresetSelect.value = presetId;
    WebChatImg2imgPreset?.syncVisibility?.(this);
    await this._applyConversationPreset(presetId);
    WebChatImg2imgPreset?.syncVisibility?.(this);
  }

  async _applyConversationPreset(presetId) {
    if (!this.currentConvId) return;
    if (String(this.currentConv?.preset_id) === String(presetId)) {
      WebChatImg2imgPreset?.syncVisibility?.(this);
      return;
    }
    try {
      this.currentConv = await this.api(`/api/conversations/${this.currentConvId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset_id: presetId }),
      });
      if (this.$.convPresetSelect) this.$.convPresetSelect.value = presetId;
      if (this.$.chatPresetSelect) this.$.chatPresetSelect.value = presetId;
      WebChatImg2imgPreset?.logDiagnostics?.(this, 'preset_patched', { presetId });
    } catch (err) {
      this.showError(err.message || 'Не удалось сменить пресет');
      this.populateConvPresetSelect(this.currentConv?.preset_id);
    } finally {
      WebChatImg2imgPreset?.syncVisibility?.(this);
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
    this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
    this.renderConvList();
  }

  async loadTrash() {
    try {
      this.trashConversations = await this.api('/api/conversations/trash');
      this._renderTrashList();
      this._updateTrashBadge();
    } catch (err) {
      this.log?.warn('trash', err.message);
      this.trashConversations = [];
      this._renderTrashList();
      this._updateTrashBadge();
    }
  }

  _updateTrashBadge() {
    const n = this.trashConversations.length;
    if (!this.$.convTrashCount) return;
    this.$.convTrashCount.textContent = String(n);
    this.$.convTrashCount.classList.toggle('hidden', n === 0);
    if (this.$.convTrashEmpty) {
      this.$.convTrashEmpty.classList.toggle('hidden', n > 0);
    }
    if (this.$.convTrashEmptyAll) {
      this.$.convTrashEmptyAll.classList.toggle('hidden', n === 0);
      if (n === 0) {
        this._cancelPendingEmptyTrash();
      }
    }
    this.$.convTrashTabBtn?.classList.toggle('has-items', n > 0);
  }

  _setSidebarTab(tab) {
    const showTrash = tab === 'trash';
    this._trashOpen = showTrash;
    this.$.convTrashPanel?.classList.toggle('hidden', !showTrash);
    this.$.convTrashTabBtn?.classList.toggle('is-active', showTrash);
    this.$.convTrashTabBtn?.setAttribute('aria-pressed', showTrash ? 'true' : 'false');
    this.$.convSidebarSheet?.classList.toggle('trash-tab-open', showTrash);
    if (showTrash) {
      this._closeConvSearchPanel();
      void this.loadTrash();
    }
  }

  _toggleTrashPanel() {
    this._setSidebarTab(this._trashOpen ? 'conversations' : 'trash');
  }

  _cancelPendingEmptyTrash() {
    if (!this._pendingEmptyTrash) return;
    this._pendingEmptyTrash = false;
    this.$.convTrashEmptyAll?.classList.remove('delete-armed');
    if (this.$.convTrashEmptyAll) {
      this.$.convTrashEmptyAll.title = 'Удалить все беседы из корзины навсегда';
    }
  }

  _onEmptyTrashClick() {
    if (!this.trashConversations.length) return;
    if (this._pendingEmptyTrash) {
      void this._executeEmptyTrash();
      return;
    }
    this._cancelPendingTrashDelete();
    this._cancelPendingDelete();
    this._cancelPendingMessageDelete();
    this._cancelMessageImageDelete();
    this._pendingEmptyTrash = true;
    this.$.convTrashEmptyAll?.classList.add('delete-armed');
    if (this.$.convTrashEmptyAll) {
      this.$.convTrashEmptyAll.title = 'Нажмите ещё раз — удалить всё навсегда';
    }
  }

  async _executeEmptyTrash() {
    this._cancelPendingEmptyTrash();
    const snapshot = [...this.trashConversations];
    this.trashConversations = [];
    this._renderTrashList();
    try {
      const result = await this.api('/api/conversations/trash', { method: 'DELETE' });
      const n = result?.deleted ?? snapshot.length;
      this.log?.info('trash', `Корзина очищена: ${n} бесед`);
    } catch (err) {
      this.trashConversations = snapshot;
      this._renderTrashList();
      this.showError(err.message || 'Не удалось очистить корзину');
    }
  }

  _renderTrashList() {
    if (!this.$.convTrashList) return;
    this._cancelPendingTrashDelete();
    const days = this.config?.trash_retention_days || 3;
    const html = this.trashConversations
      .map((c) => {
        const deletedAt = c.deleted_at ? new Date(c.deleted_at) : null;
        let meta = WebChatDateTime.formatDateTime(c.deleted_at || c.updated_at);
        if (deletedAt) {
          const purgeAt = new Date(deletedAt.getTime() + days * 86400000);
          const leftMs = purgeAt.getTime() - Date.now();
          const leftDays = Math.max(0, Math.ceil(leftMs / 86400000));
          meta += leftDays > 0 ? ` · ещё ${leftDays} дн.` : ' · скоро удалится';
        }
        return `<li class="conv-trash-item" data-id="${c.id}" role="listitem">
          <div class="conv-trash-item-row">
            <div class="conv-trash-item-main">
              <div class="conv-trash-item-title">${this.escape(c.title)}</div>
              <div class="conv-trash-item-meta">${meta}</div>
            </div>
            <div class="conv-trash-item-actions">
              <button type="button" class="conv-trash-restore" data-id="${c.id}" title="Восстановить" aria-label="Восстановить">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
              </button>
              <button type="button" class="conv-trash-delete" data-id="${c.id}" title="Удалить навсегда" aria-label="Удалить навсегда">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
          </div>
        </li>`;
      })
      .join('');
    this.$.convTrashList.innerHTML = html;
    this._updateTrashBadge();

    this.$.convTrashList.querySelectorAll('.conv-trash-restore').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        void this._restoreTrashConversation(btn.dataset.id);
      });
    });
    this.$.convTrashList.querySelectorAll('.conv-trash-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._onTrashDeleteClick(btn.dataset.id, btn);
      });
    });
  }

  _cancelPendingTrashDelete() {
    if (!this._pendingTrashDeleteId) return;
    this._pendingTrashDeleteBtn?.classList.remove('delete-armed');
    if (this._pendingTrashDeleteBtn) {
      this._pendingTrashDeleteBtn.title = 'Удалить навсегда';
    }
    this.$.convTrashList
      ?.querySelector(`.conv-trash-item[data-id="${this._pendingTrashDeleteId}"]`)
      ?.classList.remove('delete-pending');
    this._pendingTrashDeleteId = null;
    this._pendingTrashDeleteBtn = null;
  }

  _onTrashDeleteClick(id, btn) {
    if (this._pendingTrashDeleteId === id) {
      void this._executePermanentTrashDelete(id);
      return;
    }
    this._cancelPendingEmptyTrash();
    this._cancelPendingTrashDelete();
    this._cancelPendingDelete();
    this._cancelPendingMessageDelete();
    this._cancelMessageImageDelete();
    this._pendingTrashDeleteId = id;
    this._pendingTrashDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    this.$.convTrashList
      ?.querySelector(`.conv-trash-item[data-id="${id}"]`)
      ?.classList.add('delete-pending');
  }

  async _executePermanentTrashDelete(id) {
    this._cancelPendingTrashDelete();
    const idx = this.trashConversations.findIndex((c) => c.id === id);
    const removed = idx >= 0 ? this.trashConversations[idx] : null;
    if (idx >= 0) {
      this.trashConversations.splice(idx, 1);
      this._renderTrashList();
    }
    try {
      await this.api(`/api/conversations/${id}/permanent`, { method: 'DELETE' });
      this.log?.info('trash', `Беседа ${id} удалена навсегда`);
    } catch (err) {
      if (removed) {
        this.trashConversations.splice(idx, 0, removed);
        this._renderTrashList();
      }
      this.showError(err.message || 'Не удалось удалить');
    }
  }

  async _restoreTrashConversation(id) {
    try {
      const conv = await this.api(`/api/conversations/${id}/restore`, { method: 'POST' });
      this.trashConversations = this.trashConversations.filter((c) => c.id !== id);
      this._upsertConversationInList(conv);
      this.renderConvList();
      this._renderTrashList();
      await this.selectConversation(conv.id, { prefetchedConversation: conv });
      this.log?.info('trash', `Беседа ${id} восстановлена`);
    } catch (err) {
      this.showError(err.message || 'Не удалось восстановить');
    }
  }

  _conversationsFingerprintFrom(conversations) {
    return (conversations || [])
      .map((c) => `${c.id}|${c.updated_at}|${c.in_progress ? 1 : 0}|${c.title}`)
      .join(';');
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

  async _syncConversationsFromServer() {
    try {
      const list = await this.api('/api/conversations');
      const fp = this._conversationsFingerprintFrom(list);
      if (fp === this._conversationsFingerprint) return;
      this._conversationsFingerprint = fp;
      const prevId = this.currentConvId;
      this.conversations = list;
      if (prevId) {
        const updated = list.find((c) => c.id === prevId);
        if (updated) {
          this.currentConv = { ...this.currentConv, ...updated };
          this._setSettingsChatTitle(updated.title);
        }
      }
      this.renderConvList();
    } catch (err) {
      this.log?.warn('sync', err.message);
    }
  }

  _messagesFingerprintFromList(messages) {
    if (!messages?.length) return '0';
    const last = messages[messages.length - 1];
    const streaming = messages.some(
      (m) => m.role === 'assistant' && m.content_json?.streaming,
    );
    return `${messages.length}:${last.id}:${last.role}:${streaming ? 1 : 0}:${(last.content_text || '').length}`;
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
    const newRow = this._tagMessageRow(
      this._messageRowFromDb(m, { activeStreamingId }),
      m,
    );
    row.replaceWith(newRow);
    return true;
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

  renderConvList() {
    this._cancelPendingDelete();
    const empty = !this.conversations.length;
    this.$.convEmpty.classList.toggle('hidden', !empty);

    const newChatRow = this.$.convList.querySelector('.conv-new-item');
    const convItemsHtml = this.conversations
      .map((c) => {
        const active = c.id === this.currentConvId ? ' active' : '';
        const generating = c.in_progress ? ' is-generating' : '';
        const date = WebChatDateTime.formatDateTime(c.updated_at);
        return `<li class="conv-item${active}${generating}" data-id="${c.id}" role="listitem">
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
            <button type="button" class="conv-item-delete" data-id="${c.id}" title="Удалить в корзину" aria-label="Удалить в корзину">
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
      const prevTitle = conv.title;
      if (save && next !== prevTitle) {
        if (textEl) textEl.textContent = next;
        else titleEl.textContent = next;
        if (tipBody) tipBody.textContent = next;
        titleEl.setAttribute('aria-label', next);
      } else {
        if (textEl) textEl.textContent = prevTitle;
        else titleEl.textContent = prevTitle;
        if (tipBody) tipBody.textContent = prevTitle;
        titleEl.setAttribute('aria-label', prevTitle);
      }
      titleEl.classList.remove('hidden');
      input.remove();
      this._inlineTitleConvId = null;
      this._updateConvTitleTooltips();
      if (save && next !== prevTitle) {
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
    const conv = this.conversations.find((c) => c.id === convId);
    const prevTitle = conv?.title;
    const prevCurrentTitle = this.currentConvId === convId ? this.currentConv?.title : null;

    if (conv) conv.title = title;
    if (this.currentConvId === convId && this.currentConv) {
      this.currentConv = { ...this.currentConv, title };
      this._setSettingsChatTitle(title);
    }
    this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
    this.renderConvList();

    try {
      const updated = await this.api(`/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (conv) conv.title = updated.title;
      if (this.currentConvId === convId) {
        this.currentConv = updated;
        this._setSettingsChatTitle(updated.title);
      }
      this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
      this.renderConvList();
    } catch (err) {
      if (conv && prevTitle !== undefined) conv.title = prevTitle;
      if (this.currentConvId === convId && this.currentConv && prevCurrentTitle !== null) {
        this.currentConv = { ...this.currentConv, title: prevCurrentTitle };
        this._setSettingsChatTitle(prevCurrentTitle);
      }
      this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
      this.renderConvList();
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
    this._setSidebarTab('conversations');
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

  _showConvSearchSkeleton() {
    const el = this.$.convSearchResults;
    if (!el) return;
    el.classList.remove('hidden');
    el.innerHTML = Array.from({ length: 3 }, () => (
      `<li class="conv-search-skeleton" aria-hidden="true">
        <div class="conv-search-skeleton-line conv-search-skeleton-line--title"></div>
        <div class="conv-search-skeleton-line conv-search-skeleton-line--snippet"></div>
      </li>`
    )).join('');
  }

  async _runConvSearch(q) {
    if (!this.$.convSearchResults) return;
    this._showConvSearchSkeleton();
    try {
      const hits = await this.api(`/api/search?q=${encodeURIComponent(q)}`);
      this._renderSearchResults(hits, q);
    } catch (err) {
      this.showError(err.message);
      this._clearConvSearch();
    }
  }

  _renderSearchResults(hits, q) {
    const el = this.$.convSearchResults;
    if (!el) return;
    el.classList.remove('hidden');
    if (!hits.length) {
      el.innerHTML = `<li class="conv-search-empty" role="listitem">
        <p class="conv-search-empty-title">Ничего не найдено</p>
        <p class="conv-search-empty-hint">Попробуйте другие слова или проверьте орфографию</p>
      </li>`;
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
    await this.selectConversation(conversationId, { scrollToMessageId: messageId || null });
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

  _onDeleteBtnClick(id, btn) {
    if (this._pendingDeleteConvId === id) {
      void this._executeDeleteConversation(id);
      return;
    }
    this._cancelPendingMessageDelete();
    this._cancelMessageImageDelete();
    this._cancelPendingEmptyTrash();
    this._cancelPendingTrashDelete();
    this._cancelPendingDelete();
    this._pendingDeleteConvId = id;
    btn.title = 'Нажмите ещё раз — в корзину';
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
      this._pendingDeleteBtn.title = 'Удалить в корзину';
    }
    this._pendingDeleteConvId = null;
    this._pendingDeleteBtn = null;
  }

  _snapshotConversationListState() {
    return {
      conversations: [...this.conversations],
      fingerprint: this._conversationsFingerprint,
      currentConvId: this.currentConvId,
      currentConv: this.currentConv ? { ...this.currentConv } : null,
    };
  }

  _restoreConversationListState(snapshot) {
    this.conversations = snapshot.conversations;
    this._conversationsFingerprint = snapshot.fingerprint;
    this.renderConvList();
  }

  /**
   * Мгновенно убрать беседу из списка (оптимистично).
   * @returns {{ snapshot, nextConvId: string|null }}
   */
  _optimisticRemoveConversation(id) {
    const snapshot = this._snapshotConversationListState();
    const wasCurrent = this.currentConvId === id;
    const remaining = this.conversations.filter((c) => c.id !== id);
    const nextConvId = wasCurrent && remaining.length ? remaining[0].id : null;

    this.conversations = remaining;
    this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
    WebChatComposer.clearDraft(this, id);
    this._clearScrollPosition(id);

    if (wasCurrent) {
      this.disconnectSocket();
      if (nextConvId) {
        void this.selectConversation(nextConvId);
      } else {
        this._clearCurrentConversation();
      }
    }

    this.renderConvList();
    return { snapshot, nextConvId, wasCurrent };
  }

  async _executeDeleteConversation(id) {
    this._cancelPendingDelete();
    if (this._deletingConvIds.has(id)) return;
    this._deletingConvIds.add(id);

    const { snapshot, wasCurrent } = this._optimisticRemoveConversation(id);
    this.log?.info('chat', `Удаление беседы ${id}`);

    try {
      await this.api(`/api/conversations/${id}`, { method: 'DELETE' });
      void this.loadTrash();
      void this._syncConversationsFromServer();
    } catch (err) {
      this._restoreConversationListState(snapshot);
      if (wasCurrent && snapshot.currentConvId === id && snapshot.currentConv) {
        this.currentConvId = id;
        this.currentConv = snapshot.currentConv;
        localStorage.setItem('webchat_conv_id', id);
        void this.selectConversation(id, { prefetchedConversation: snapshot.currentConv });
      }
      this.showError(err.message || 'Не удалось удалить беседу');
    } finally {
      this._deletingConvIds.delete(id);
    }
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
    WebChatComposer.closeToolsMenu(this);
    this._hideRagPreview();
    if (this.streaming) this.endStreaming();
    WebChatComposer.syncSendState(this);
  }

  openNewConvModal() {
    document.getElementById('new-conv-title').value = '';
    this.$.newConvModal.showModal();
    this._bindDialogFocusTrap(this.$.newConvModal);
    setTimeout(() => document.getElementById('new-conv-title').focus(), 50);
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

  _upsertConversationInList(conv) {
    const idx = this.conversations.findIndex((c) => c.id === conv.id);
    if (idx >= 0) {
      this.conversations[idx] = { ...this.conversations[idx], ...conv };
    } else {
      this.conversations.unshift(conv);
    }
    this._conversationsFingerprint = this._conversationsFingerprintFrom(this.conversations);
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

  async createConversation() {
    const title = document.getElementById('new-conv-title').value.trim() || 'Новая беседа';
    const presetId = this.$.newConvPreset.value || null;
    const body = { title };
    if (presetId) body.preset_id = presetId;
    this.$.newConvModal.close();
    const conv = await this.api('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    this._upsertConversationInList(conv);
    await this.selectConversation(conv.id, { prefetchedConversation: conv });
    void this.loadConversations();
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
        WebChatComposer.restoreDraft(this, id);
        WebChatComposer.restorePendingFromSession(this);
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
      this.showError(err.message || 'Не удалось открыть беседу');
      this.log?.error('chat', err.message || 'selectConversation failed');
    } finally {
      this._endConvSwitchOverlay();
    }
  }

  /**
   * @param {{
   *   preserveScroll?: boolean,
   *   scrollToEnd?: boolean,
   *   restoreScrollEntry?: object|null,
   *   force?: boolean,
   * }} [opts]
   */
  async loadMessages(opts = {}) {
    const scrollEl = this._chatHistoryScrollEl();
    const restoreEntry = opts.restoreScrollEntry || null;
    const wantScrollEnd = restoreEntry
      ? false
      : (
        opts.scrollToEnd === true
        || (opts.scrollToEnd !== false && this._scrollStuckToBottom)
      );
    const preserve =
      opts.preserveScroll === true
      || (
        opts.preserveScroll !== false
        && !wantScrollEnd
        && !restoreEntry
      );
    const scrollTopBefore = scrollEl?.scrollTop ?? 0;
    const scrollHeightBefore = scrollEl?.scrollHeight ?? 0;

    const showSkeleton = !preserve
      && !opts.force
      && !this._messagesFingerprint
      && !this.streaming
      && !this._generationResumeActive;
    if (showSkeleton) {
      this._showMessagesSkeleton();
    }

    const messages = await this.api(`/api/conversations/${this.currentConvId}/messages?limit=100`);
    this._messagesLoading = false;
    this.$.chatMessages?.classList.remove('is-loading');
    const mustRenderDom = showSkeleton || this._isMessagesSkeletonVisible();
    const listFp = this._messagesFingerprintFromList(messages);
    const structureKey = this._messagesStructureKey(messages);
    const activeStreamingId = opts.activeStreamingId
      || this._activeStreamingIdFromList(messages);

    if (!opts.force && listFp === this._messagesFingerprint && !mustRenderDom) {
      return;
    }

    const domIds = this._domMessageIds();
    const serverIds = messages.map((m) => m.id);
    const sameStructure = domIds.join('|') === serverIds.join('|');

    const afterLayout = () => {
      if (restoreEntry) {
        this._applyScrollRestore(restoreEntry);
      } else if (preserve && scrollEl) {
        const delta = scrollEl.scrollHeight - scrollHeightBefore;
        scrollEl.scrollTop = scrollTopBefore + delta;
        this._onChatScroll();
      } else {
        this.scrollToBottom(opts.scrollToEnd !== false);
      }
    };

    if (sameStructure && !opts.force) {
      let changed = false;
      let domComplete = domIds.length === serverIds.length;
      for (const m of messages) {
        if (!this._findRow(m.id)) {
          domComplete = false;
          break;
        }
        if (this._patchMessageRowIfNeeded(m, { activeStreamingId })) changed = true;
      }
      if (domComplete && !mustRenderDom) {
        this.$.chatMessages.dataset.structureKey = structureKey;
        this._messagesFingerprint = listFp;
        if (changed || restoreEntry) afterLayout();
        return;
      }
    }

    const canAppendOnly = domIds.length > 0
      && serverIds.length >= domIds.length
      && serverIds.slice(0, domIds.length).join('|') === domIds.join('|');

    if (canAppendOnly && !opts.force) {
      for (let i = domIds.length; i < messages.length; i += 1) {
        this.$.chatMessages.appendChild(
          this._tagMessageRow(
            this._messageRowFromDb(messages[i], { activeStreamingId }),
            messages[i],
          ),
        );
      }
      this.$.chatMessages.dataset.structureKey = structureKey;
      this._messagesFingerprint = listFp;
      afterLayout();
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const m of messages) {
      fragment.appendChild(
        this._tagMessageRow(
          this._messageRowFromDb(m, { activeStreamingId }),
          m,
        ),
      );
    }
    this.$.chatMessages.replaceChildren(fragment);
    this.$.chatMessages.dataset.structureKey = structureKey;
    this._messagesFingerprint = listFp;
    afterLayout();
  }

  appendMessageFromDb(m) {
    this.$.chatMessages.appendChild(this._messageRowFromDb(m));
  }

  _messageRowFromDb(m, { activeStreamingId = null } = {}) {
    const urls = imageUrlsFromMessage(m);
    if (m.role === 'user') {
      return this._buildUserRow(this._userMessageDisplayText(m), m.id, urls);
    }
    const ragHits = m.content_json?.rag_sources;
    const reasoning = m.content_json?.reasoning;
    const isStreamingDraft = Boolean(m.content_json?.streaming)
      || (activeStreamingId && m.id === activeStreamingId);
    if (m.role === 'assistant' && isStreamingDraft) {
      if (activeStreamingId && m.id !== activeStreamingId) {
        return this._buildAssistantRow(m.content_text || '', urls, m.id, ragHits, reasoning);
      }
      return this._buildAssistantDraftRow(m, urls);
    }
    if (m.role === 'assistant') {
      return this._buildAssistantRow(m.content_text || '', urls, m.id, ragHits, reasoning);
    }
    const fallback = document.createElement('div');
    fallback.className = 'message-row';
    return fallback;
  }

  /** Классы has-content / has-images / has-reasoning — для ширины пузыря и сетки картинок. */
  _syncAssistantLayoutClasses(el) {
    if (!el?.classList?.contains('assistant')) return;
    const bubble = el.querySelector('.message-bubble');
    const grid = el.querySelector('.message-images');
    const hasContent = Boolean(bubble?.textContent?.trim());
    const hasImages = Boolean(grid?.children?.length);
    const hasReasoning = Boolean(el.querySelector('.message-reasoning'));
    el.classList.toggle('has-content', hasContent);
    el.classList.toggle('has-images', hasImages);
    el.classList.toggle('has-reasoning', hasReasoning);
  }

  _ensureAssistantStreamShell(el) {
    let bubble = el.querySelector('.message-bubble');
    if (!bubble) {
      bubble = document.createElement('div');
      bubble.className = 'message-bubble';
    }
    let images = el.querySelector('.message-images');
    if (!images) {
      images = document.createElement('div');
      images.className = 'message-images';
    }
    if (!el.querySelector('.message-status')) {
      el.insertAdjacentHTML('beforeend', MESSAGE_STATUS_HTML);
    }
    const status = el.querySelector('.message-status');
    el.append(bubble, images, status);
    return el;
  }

  _fillAssistantBubble(el, text, imageUrls, reasoning = null) {
    const displayText = assistantDisplayText(text || '');
    const bubble = el.querySelector('.message-bubble');
    const grid = el.querySelector('.message-images');
    el.dataset.rawContent = text || '';
    const reasoningBlock = WebChatMessageBlocks.buildMessageReasoning(reasoning);
    const existingReasoning = el.querySelector('.message-reasoning');
    if (reasoningBlock) {
      if (existingReasoning) existingReasoning.replaceWith(reasoningBlock);
      else el.insertBefore(reasoningBlock, el.firstChild);
    } else if (existingReasoning) {
      existingReasoning.remove();
    }
    if (displayText && bubble) {
      bubble.innerHTML = formatMarkdown(displayText);
    } else if (bubble) {
      bubble.innerHTML = '';
    }
    if (grid) {
      this._setGridImages(grid, imageUrls || []);
    }
    this._syncAssistantLayoutClasses(el);
    this._bindImageClicks(el);
  }

  appendAssistantDraftFromDb(m, imageUrls = null) {
    this.$.chatMessages.appendChild(this._buildAssistantDraftRow(m, imageUrls));
  }

  _buildAssistantDraftRow(m, imageUrls = null) {
    const urls = imageUrls ?? imageUrlsFromMessage(m);
    const el = document.createElement('div');
    el.className = 'chat-message assistant';
    this._ensureAssistantStreamShell(el);
    this._fillAssistantBubble(el, m.content_text || '', urls, m.content_json?.reasoning);
    const row = this._wrapMessage('assistant', el, m.id);
    row.dataset.streamingDraft = 'true';
    return row;
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
      onToolDone: () => this.onToolDone(),
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
    if (!this.socket) this.connectSocket();
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

    if (!(await this._ensureSocketReady())) {
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
        this._bindImageClicks(row);
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
    this._bindImageClicks(el);
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
      ${MESSAGE_STATUS_HTML}
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

  _renderStreamTextToBubble(text) {
    if (!this.streamEl) return;
    const body = text ?? this.streamText ?? '';
    this.streamText = body;
    if (this.streamEl.dataset) {
      this.streamEl.dataset.rawContent = body;
    }
    const bubble = this.streamEl.querySelector('.message-bubble');
    const displayText = assistantDisplayText(body);
    if (displayText && bubble) {
      bubble.innerHTML = formatMarkdown(displayText);
    } else if (bubble) {
      bubble.innerHTML = '';
    }
    this._syncAssistantLayoutClasses(this.streamEl);
    bubble?.querySelectorAll('img').forEach((img) => {
      const src = img.getAttribute('src');
      if (src) {
        img.dataset.url = mediaFullUrl(src);
        img.src = mediaPreviewUrl(src);
      }
    });
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
    const hasVisibleText = Boolean(assistantDisplayText(this.streamText || ''));
    if (!hasVisibleText) {
      this._setUiActivityStage('llm_thinking');
    }
    this.streamReasoningText = (this.streamReasoningText || '') + chunk;
    this._renderStreamReasoning(this.streamReasoningText);
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
    if (!this._ensureStreamTarget() || !this.streamImagesEl) return;
    const added = this._appendImagesToGrid(this.streamImagesEl, urls);
    if (added > 0) {
      this._generationHadImages = true;
      this.hideProgress();
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

  onToolDone() {
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
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=30`,
      );
      const draft = messages.find((m) => m.id === messageId);
      if (draft) {
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
      const displayText = assistantDisplayText(this.streamText);
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
    const hasText = Boolean(assistantDisplayText(this.streamText || ''));
    const hasImages = Boolean(this.streamImagesEl?.children.length);

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
    if (hasText || hasImages) {
      this.hideProgress();
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
      if (this.streaming || this._generationResumeActive) {
        this._generationNotifyPending = true;
        await this._completeGenerationUi({ preserveScroll: !this._scrollStuckToBottom });
        this._notifyGenerationComplete({ conversationTitle: this.currentConv?.title });
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
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const draft = messages.find((m) => m.id === streamId);
      if (draft) {
        this.$.chatMessages.appendChild(this._buildAssistantDraftRow(draft));
        bound = this._bindStreamToMessageId(draft.id);
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

    this._syncResumeProgress(status);
    await this._refreshStreamingBubbleFromServer();
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
      this.hideProgress();
    }

    const urls = imageUrlsFromMessage(target);
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
    this._clearGenerationSyncTimer();
    this._generationResumeActive = false;
    this.hideProgress();
    this._notifyGenerationComplete({ conversationTitle });
    this._regenerating = false;

    const afterReload = () => {
      void this._attachRagSourcesAfterTurn(assistantMessageId);
      this.endStreaming();
      if (conversationTitle && this.currentConv) {
        this.currentConv.title = conversationTitle;
        const conv = this.conversations.find((c) => c.id === this.currentConvId);
        if (conv) conv.title = conversationTitle;
        this._setSettingsChatTitle(conversationTitle);
      }
      this._conversationsFingerprint = '';
      void this.loadConversations();
    };

    const hasLiveStream = Boolean(
      this.streamRow && (this.streamText || this.streamImagesEl?.children.length),
    );
    const hadRegenerate = this._regenerating;

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
    try {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const target = messages.find((m) => m.id === messageId);
      if (!target) return;
      const serverText = target.content_text || '';
      if (serverText.length >= (this.streamText || '').length) {
        this._renderStreamTextToBubble(serverText);
      }
      const urls = imageUrlsFromMessage(target);
      if (urls.length && this.streamImagesEl) {
        this._syncStreamImagesFromServer(urls);
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
    this._settleAllStreamRows();
    this.endStreaming();
  }

  _settleStreamElement(el) {
    if (!el) return;
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
      this.showError(message || 'Генерация отменена', 4000, opts);
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

  _buildMessageActions(role, row) {
    const wrap = document.createElement('div');
    wrap.className = 'message-actions';
    wrap.setAttribute('role', 'toolbar');
    wrap.setAttribute('aria-label', 'Действия с сообщением');

    for (const key of this._messageActionKeysForRow(role, row)) {
      const def = MESSAGE_ACTION_DEFS.find((d) => d.key === key);
      if (!def) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `msg-action-btn btn-icon${def.danger ? ' danger' : ''}`;
      btn.dataset.action = def.key;
      btn.title = def.title;
      btn.setAttribute('aria-label', def.label);
      btn.innerHTML = def.icon;
      wrap.appendChild(btn);
    }

    wrap.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      const hostRow = wrap.closest('.message-row');
      if (!btn || !hostRow?.dataset.messageId) return;
      e.stopPropagation();
      const id = hostRow.dataset.messageId;
      const action = btn.dataset.action;
      if (action === 'copy') this.copyMessageText(id, role, btn);
      else if (action === 'delete') this._onMessageDeleteClick(id, role, btn);
      else if (action === 'edit') this.editMessage(id, role);
      else if (action === 'regenerate') this.regenerateMessage(id);
    });

    return wrap;
  }

  _messageActionKeysForRow(role, row) {
    const keys = [];
    if (this._hasCopyableMessageText(row, role)) keys.push('copy');
    const hasId = Boolean(row?.dataset?.messageId);
    const isActiveStream = this.streaming && row === this.streamRow;
    if (hasId && !isActiveStream) {
      keys.push('edit', 'regenerate');
    }
    keys.push('delete');
    return keys;
  }

  _syncMessageActions(row, role) {
    if (!row || !this._shouldAttachMessageActions(row)) return;
    const actions = row.querySelector('.message-actions');
    const keys = this._messageActionKeysForRow(role, row);
    if (!actions) {
      row.appendChild(this._buildMessageActions(role, row));
      return;
    }
    const current = [...actions.querySelectorAll('[data-action]')].map((b) => b.dataset.action);
    if (keys.length === current.length && keys.every((k) => current.includes(k))) return;
    actions.remove();
    row.appendChild(this._buildMessageActions(role, row));
  }

  _syncAllMessageActions() {
    this.$.chatMessages?.querySelectorAll('.message-row[data-message-id]').forEach((row) => {
      const role = row.classList.contains('user') ? 'user' : 'assistant';
      this._syncMessageActions(row, role);
    });
  }

  _hasCopyableMessageText(row, role) {
    return Boolean(this._extractMessagePlainText(row, role));
  }

  _shouldAttachMessageActions(row) {
    if (!row) return false;
    if (row.dataset.streamingDraft === 'true' || row.dataset.temp === 'true') return false;
    if (this.streaming && row === this.streamRow) return false;
    return true;
  }

  _attachActions(row, role) {
    if (!this._shouldAttachMessageActions(row)) return;
    this._syncMessageActions(row, role);
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
    this.$.chatMessages.appendChild(this._buildUserRow(text, messageId, imageUrls));
    this._scheduleScrollToBottom(true);
  }

  _buildUserRow(text, messageId = null, imageUrls = []) {
    const el = document.createElement('div');
    el.className = 'chat-message user';
    if (text) {
      const textEl = document.createElement('div');
      textEl.className = 'user-text';
      textEl.dataset.rawText = text;
      this.promptMacros.renderUserText(textEl, text);
      el.appendChild(textEl);
    }
    const urls = [...new Set(imageUrls.map(mediaFullUrl))];
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(this._createImage(url, { scrollOnLoad: false }));
      el.appendChild(grid);
      this._bindImageClicks(el);
    }
    return this._wrapMessage('user', el, messageId);
  }

  addAssistantBubble(text, imageUrls, messageId = null) {
    this.$.chatMessages.appendChild(this._buildAssistantRow(text, imageUrls, messageId));
    this._scheduleScrollToBottom(true);
  }

  _buildAssistantRow(text, imageUrls, messageId = null, ragHits = null, reasoning = null) {
    const el = document.createElement('div');
    el.className = 'chat-message assistant';
    el.dataset.rawContent = text || '';
    const displayText = assistantDisplayText(text);
    const urls = [...new Set(imageUrls.map(mediaFullUrl).filter(Boolean))];
    const reasoningBlock = WebChatMessageBlocks.buildMessageReasoning(reasoning);
    if (reasoningBlock) el.appendChild(reasoningBlock);
    if (displayText) {
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.innerHTML = formatMarkdown(displayText);
      el.appendChild(bubble);
    }
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(this._createImage(url, { scrollOnLoad: false }));
      el.appendChild(grid);
    }
    const ragBlock = WebChatMessageBlocks.buildMessageRagSources(ragHits);
    if (ragBlock) el.appendChild(ragBlock);
    this._syncAssistantLayoutClasses(el);
    this._bindImageClicks(el);
    return this._wrapMessage('assistant', el, messageId);
  }

  async _attachRagSourcesAfterTurn(assistantMessageId) {
    if (!assistantMessageId || !this.currentConvId) return;

    const row = this._findRow(assistantMessageId);
    const msgEl = row?.querySelector('.chat-message.assistant');
    if (!msgEl || msgEl.querySelector('.message-rag-sources')) return;

    let hits;
    try {
      const messages = await this.api(
        `/api/conversations/${this.currentConvId}/messages?limit=50`,
      );
      const target = messages.find((m) => m.id === assistantMessageId);
      hits = target?.content_json?.rag_sources;
    } catch (err) {
      this.log?.warn('rag', err?.message || 'rag sources load failed');
      return;
    }
    if (!hits?.length) return;

    const block = WebChatMessageBlocks.buildMessageRagSources(hits);
    if (block) msgEl.appendChild(block);
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

  _onMessageDeleteClick(messageId, role, btn) {
    if (this._pendingDeleteMessageId === messageId) {
      void this._executeDeleteMessage(messageId, role);
      return;
    }
    this._cancelPendingMessageDelete();
    this._cancelPendingDelete();
    this._cancelMessageImageDelete();
    this._pendingDeleteMessageId = messageId;
    this._pendingDeleteMessageRole = role;
    this._pendingDeleteMessageBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    this._findRow(messageId)?.classList.add('delete-pending');
  }

  _cancelPendingMessageDelete() {
    if (!this._pendingDeleteMessageId) return;
    this._pendingDeleteMessageBtn?.classList.remove('delete-armed');
    if (this._pendingDeleteMessageBtn) {
      this._pendingDeleteMessageBtn.title = 'Удалить';
    }
    this._findRow(this._pendingDeleteMessageId)?.classList.remove('delete-pending');
    this._pendingDeleteMessageId = null;
    this._pendingDeleteMessageBtn = null;
    this._pendingDeleteMessageRole = null;
  }

  async _executeDeleteMessage(messageId, role) {
    this._cancelPendingMessageDelete();
    const row = this._findRow(messageId);
    if (!row) return;

    if (this.editingMessageId === messageId) {
      this._cancelMessageEdit();
    }

    if (this.streaming) {
      this.socket?.cancel();
    }

    const cascade = role === 'user';
    this.log?.info('msg', `Удаление ${role} ${messageId} cascade=${cascade}`);

    if (cascade) {
      this._removeFollowingRows(row, true);
    } else {
      row.remove();
    }
    this._messagesFingerprint = `opt-del-${Date.now()}`;

    try {
      await this.api(
        `/api/conversations/${this.currentConvId}/messages/${messageId}?cascade=${cascade}`,
        { method: 'DELETE' },
      );
    } catch (err) {
      this.showError(err.message);
      if (this.currentConvId) {
        await this.loadMessages({ force: true, preserveScroll: true });
      }
    }
  }

  async editMessage(messageId, role) {
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
      await this._enterUserMessageEdit(messageId, row, text);
      return;
    }

    this.$.userInput.placeholder = 'Enter — сохранить изменения';
    this.editingMessageId = messageId;
    this.editingRole = role;
    this.$.userInput.value = text.trim();
    WebChatComposer.autoResizeInput(this);
    this.$.userInput.focus();
  }

  async _enterUserMessageEdit(messageId, row, text) {
    this._editComposerBackup = {
      text: this.$.userInput.value,
      attachments: this.pendingAttachments.map((a) => ({ ...a })),
    };
    WebChatComposer.resetUi(this);

    this.editingMessageId = messageId;
    this.editingRole = 'user';
    row.classList.add('is-being-edited');
    row.querySelector('.message-images')?.classList.add('hidden');

    this.$.userInput.value = text.trim();
    WebChatComposer.autoResizeInput(this);
    this.$.userInput.focus();

    try {
      const attachments = await this.api(
        `/api/conversations/${this.currentConvId}/messages/${messageId}/attachments`,
      );
      for (const att of attachments) {
        if (!att?.id) continue;
        if (this.pendingAttachments.some((a) => a.id === att.id)) continue;
        this.pendingAttachments.push(att);
        WebChatComposer.renderAttachmentChip(this, att);
      }
      if (this.pendingAttachments.length) {
        this.$.attachmentStrip.classList.remove('hidden');
      }
    } catch (err) {
      this.showError(err.message || 'Не удалось загрузить вложения');
    }
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
  _previousUserMessageRow(row) {
    let el = row?.previousElementSibling;
    while (el) {
      if (el.classList?.contains('message-row') && el.classList.contains('user')) {
        return el;
      }
      el = el.previousElementSibling;
    }
    return null;
  }

  _llmTextForRegenerate(userRow) {
    if (!userRow) return null;
    const rawText = this._extractMessagePlainText(userRow, 'user');
    if (typeof WebChatImg2imgPreset === 'undefined') {
      return rawText || null;
    }
    const payloadText = WebChatImg2imgPreset.getPayloadText(this, rawText);
    WebChatImg2imgPreset.logDiagnostics?.(this, 'regenerate', {
      rawLen: rawText.length,
      payloadLen: payloadText.length,
      injected: payloadText !== rawText,
      outPreview: payloadText.slice(0, 160),
    });
    return payloadText || null;
  }

  async _runRegenerate(messageId, { fromAssistant = false } = {}) {
    if (this.streaming || !this.socket) return;
    const row = this._findRow(messageId);
    if (!row) return;

    if (fromAssistant) {
      this._removeFollowingRows(row, true);
    } else {
      this._removeFollowingRows(row, false);
    }

    const userRow = fromAssistant ? this._previousUserMessageRow(row) : row;
    const llmTextOverride = this._llmTextForRegenerate(userRow);

    this.log?.info('msg', `Перегенерация ${fromAssistant ? 'assistant' : 'user'} ${messageId}`);
    this._regenerating = true;
    this.startStreaming();
    try {
      this.socket.sendRegenerate(
        messageId,
        this.getWsIntegrationPayload(),
        llmTextOverride,
      );
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
      const resolved = mediaFullUrl(raw);
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
      const resolved = mediaFullUrl(raw);
      if (!resolved || this._gridHasImageKey(grid, resolved)) continue;
      grid.appendChild(this._createImage(resolved));
      added += 1;
    }
    return added;
  }

  /**
   * Синхронизация картинок при resume/F5: не затирать сетку, если WS уже добавил превью.
   */
  _syncStreamImagesFromServer(urls) {
    if (!this.streamImagesEl || !urls?.length) return;
    if (this.streamImagesEl.children.length === 0) {
      this._setGridImages(this.streamImagesEl, urls);
    } else {
      this._appendImagesToGrid(this.streamImagesEl, urls);
    }
    this._syncAssistantLayoutClasses(this.streamEl);
  }

  _createImage(url, { scrollOnLoad = true } = {}) {
    const full = mediaFullUrl(url);
    const preview = mediaPreviewUrl(url);
    const target = parseMediaGalleryTarget(full);
    const frame = document.createElement('div');
    frame.className = 'message-image-frame';
    frame.dataset.fullUrl = full;
    if (target) {
      frame.dataset.mediaKey = `${target.source}:${target.id}`;
    }

    const img = document.createElement('img');
    img.src = preview;
    img.dataset.url = full;
    img.alt = 'Изображение';
    img.loading = 'lazy';
    img.decoding = 'async';
    if (scrollOnLoad) {
      img.addEventListener('load', () => this._scheduleScrollToBottom(), { once: true });
    }
    frame.appendChild(img);

    const mediaKey = target ? `${target.source}:${target.id}` : '';
    const deleteBtn = target
      ? `<button type="button" class="gallery-card-action gallery-card-delete danger message-image-delete" data-media-key="${this.escapeAttr(mediaKey)}" title="Удалить" aria-label="Удалить">${MSG_IMAGE_ICON_DELETE}</button>`
      : '';
    const favoriteBtn = target
      ? `<button type="button" class="gallery-card-action message-image-favorite" data-media-key="${this.escapeAttr(mediaKey)}" data-full-url="${this.escapeAttr(full)}" title="В избранное" aria-label="В избранное">${MSG_IMAGE_ICON_STAR}</button>`
      : '';
    frame.insertAdjacentHTML(
      'beforeend',
      `<button type="button" class="gallery-card-action gallery-card-attach gallery-card-attach-tl message-image-attach" data-full-url="${this.escapeAttr(full)}" title="Прикрепить это изображение к сообщению" aria-label="Прикрепить к сообщению">${MSG_IMAGE_ICON_ATTACH}</button>
      <div class="gallery-card-actions">
        ${favoriteBtn}
        <button type="button" class="gallery-card-action gallery-card-save message-image-save" data-full-url="${this.escapeAttr(full)}" title="Сохранить" aria-label="Сохранить">${MSG_IMAGE_ICON_SAVE}</button>
        ${deleteBtn}
      </div>`,
    );
    if (target) {
      void this._syncFavoriteVisualByUrl(full, frame.querySelector('.message-image-favorite'));
    }
    return frame;
  }

  _bindMessageImageActions() {
    if (!this.$.chatMessages || this._messageImageActionsBound) return;
    this._messageImageActionsBound = true;

    this.$.chatMessages.addEventListener('click', (e) => {
      const attachBtn = e.target.closest('.message-image-attach');
      if (attachBtn) {
        e.preventDefault();
        e.stopPropagation();
        void this.attachImageUrlToComposer(attachBtn.dataset.fullUrl, attachBtn);
        return;
      }
      const saveBtn = e.target.closest('.message-image-save');
      if (saveBtn) {
        e.preventDefault();
        e.stopPropagation();
        void this._saveMessageImage(saveBtn.dataset.fullUrl);
        return;
      }
      const favoriteBtn = e.target.closest('.message-image-favorite');
      if (favoriteBtn) {
        e.preventDefault();
        e.stopPropagation();
        void this._toggleFavoriteByUrl(favoriteBtn.dataset.fullUrl, favoriteBtn);
        return;
      }
      const deleteBtn = e.target.closest('.message-image-delete');
      if (deleteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const frame = deleteBtn.closest('.message-image-frame');
        if (frame) this._onMessageImageDeleteClick(frame, deleteBtn);
        return;
      }
      const img = e.target.closest('.message-image-frame img');
      if (img) {
        e.preventDefault();
        this.openLightbox(img.dataset.url || mediaFullUrl(img.src));
      }
    });

    document.addEventListener('click', (e) => {
      if (!this._pendingImageDeleteKey) return;
      if (e.target.closest('.message-image-delete')) return;
      this._cancelMessageImageDelete();
    });
  }

  _cancelMessageImageDelete() {
    if (!this._pendingImageDeleteKey) return;
    this._pendingImageDeleteBtn?.classList.remove('delete-armed');
    this.$.chatMessages
      ?.querySelectorAll(`.message-image-frame[data-media-key="${CSS.escape(this._pendingImageDeleteKey)}"]`)
      .forEach((f) => f.classList.remove('delete-pending'));
    if (this._pendingImageDeleteBtn) {
      this._pendingImageDeleteBtn.title = 'Удалить';
    }
    this._pendingImageDeleteKey = null;
    this._pendingImageDeleteBtn = null;
  }

  _onMessageImageDeleteClick(frame, btn) {
    const key = frame.dataset.mediaKey;
    if (!key) return;
    if (this._pendingImageDeleteKey === key) {
      void this._executeMessageImageDelete(frame);
      return;
    }
    this._cancelPendingMessageDelete();
    this._cancelPendingDelete();
    this._cancelMessageImageDelete();
    this._pendingImageDeleteKey = key;
    this._pendingImageDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    frame.classList.add('delete-pending');
  }

  async _executeMessageImageDelete(frame) {
    const key = frame.dataset.mediaKey;
    const full = frame.dataset.fullUrl || '';
    const target = parseMediaGalleryTarget(full);
    if (!target) return;
    this._cancelMessageImageDelete();
    const path = target.source === 'db'
      ? `/api/gallery/db/${target.id}`
      : `/api/gallery/disk/${encodeURIComponent(target.filename)}`;

    const row = frame.closest('.message-row');
    const grid = row?.querySelector('.message-images');
    const msgEl = row?.querySelector('.chat-message');
    frame.remove();
    if (grid && !grid.children.length && msgEl) {
      msgEl.classList.remove('has-images');
    }

    try {
      const res = await fetch(path, { method: 'DELETE' });
      if (res.status === 404) throw new Error('Уже удалено');
      if (!res.ok && res.status !== 204) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      this._messagesFingerprint = `opt-img-${Date.now()}`;
      this.log?.info('chat', `Изображение удалено: ${key}`);
    } catch (err) {
      this.showError(err.message || 'Не удалось удалить');
      if (this.currentConvId) {
        await this.loadMessages({ preserveScroll: true });
      }
    }
  }

  async _saveMessageImage(url) {
    const full = mediaFullUrl(url);
    if (!full) return;
    try {
      const res = await fetch(full);
      if (!res.ok) throw new Error('Не удалось загрузить файл');
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = this._filenameFromLightboxUrl(full);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      this.showError(err.message || 'Не удалось скачать');
    }
  }


  _bindImageClicks(container) {
    container.querySelectorAll('.message-bubble img, .md-inline-img').forEach((img) => {
      img.addEventListener('click', (e) => {
        e.preventDefault();
        this.openLightbox(img.dataset.url || mediaFullUrl(img.src));
      });
    });
  }


  _readScrollPositions() {
    try {
      const raw = localStorage.getItem(SCROLL_POSITIONS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  _writeScrollPositions(positions) {
    try {
      localStorage.setItem(SCROLL_POSITIONS_STORAGE_KEY, JSON.stringify(positions));
    } catch (err) {
      this.log?.warn('chat', `Не удалось сохранить позицию прокрутки: ${err.message}`);
    }
  }

  _getScrollPositionEntry(convId) {
    if (!convId) return null;
    const entry = this._readScrollPositions()[convId];
    if (!entry || typeof entry !== 'object') return null;
    return entry;
  }

  _trimScrollPositions(positions) {
    const keys = Object.keys(positions);
    if (keys.length <= SCROLL_POSITIONS_MAX_ENTRIES) return positions;
    const sorted = keys
      .map((id) => ({ id, updatedAt: Number(positions[id]?.updatedAt) || 0 }))
      .sort((a, b) => b.updatedAt - a.updatedAt);
    const keep = new Set(sorted.slice(0, SCROLL_POSITIONS_MAX_ENTRIES).map((x) => x.id));
    const out = {};
    for (const id of keep) {
      out[id] = positions[id];
    }
    return out;
  }

  _findScrollAnchor(scrollEl) {
    if (!scrollEl) return null;
    const containerTop = scrollEl.getBoundingClientRect().top;
    const rows = this.$.chatMessages?.querySelectorAll('.message-row[data-message-id]');
    if (!rows?.length) return null;
    for (const row of rows) {
      const rect = row.getBoundingClientRect();
      if (rect.bottom > containerTop + 1) {
        return {
          messageId: row.dataset.messageId,
          offset: Math.round(rect.top - containerTop),
        };
      }
    }
    return null;
  }

  _saveScrollPosition(convId = this.currentConvId) {
    if (!convId || this._suppressScrollPositionSave) return;
    const scrollEl = this._chatHistoryScrollEl();
    if (!scrollEl) return;

    const positions = this._readScrollPositions();
    const dist = this._distanceFromBottom(scrollEl);
    if (dist <= SCROLL_STICKY_PX) {
      positions[convId] = { atBottom: true, updatedAt: Date.now() };
    } else {
      const anchor = this._findScrollAnchor(scrollEl);
      positions[convId] = {
        atBottom: false,
        scrollTop: Math.round(scrollEl.scrollTop),
        anchorMessageId: anchor?.messageId || null,
        anchorOffset: anchor?.offset ?? 0,
        updatedAt: Date.now(),
      };
    }
    this._writeScrollPositions(this._trimScrollPositions(positions));
  }

  _clearScrollPosition(convId) {
    if (!convId) return;
    const positions = this._readScrollPositions();
    if (!positions[convId]) return;
    delete positions[convId];
    this._writeScrollPositions(positions);
  }

  _scheduleScrollPositionSave() {
    if (!this.currentConvId || this._suppressScrollPositionSave) return;
    clearTimeout(this._scrollPositionSaveTimer);
    this._scrollPositionSaveTimer = setTimeout(
      () => this._saveScrollPosition(this.currentConvId),
      SCROLL_POSITION_SAVE_DEBOUNCE_MS,
    );
  }

  _applyScrollAnchor(entry) {
    const scrollEl = this._chatHistoryScrollEl();
    if (!scrollEl || !entry) return false;

    if (entry.anchorMessageId) {
      const row = this._findRow(entry.anchorMessageId);
      if (row) {
        const containerTop = scrollEl.getBoundingClientRect().top;
        const rowTop = row.getBoundingClientRect().top;
        const targetOffset = Number.isFinite(entry.anchorOffset) ? entry.anchorOffset : 0;
        scrollEl.scrollTop += (rowTop - containerTop) - targetOffset;
        return true;
      }
    }

    if (Number.isFinite(entry.scrollTop)) {
      scrollEl.scrollTop = entry.scrollTop;
      return true;
    }
    return false;
  }

  _settleScrollAfterImages(entry) {
    if (!entry?.anchorMessageId) return;
    const scrollEl = this._chatHistoryScrollEl();
    if (!scrollEl) return;

    const reapply = () => {
      this._suppressScrollPositionSave = true;
      this._applyScrollAnchor(entry);
      this._onChatScroll();
      this._suppressScrollPositionSave = false;
    };

    const imgs = scrollEl.querySelectorAll('.message-images img, .message-bubble img');
    let pending = 0;
    for (const img of imgs) {
      if (!img.complete) {
        pending += 1;
        const done = () => {
          pending -= 1;
          if (pending === 0) requestAnimationFrame(reapply);
        };
        img.addEventListener('load', done, { once: true });
        img.addEventListener('error', done, { once: true });
      }
    }
    requestAnimationFrame(() => {
      requestAnimationFrame(reapply);
    });
  }

  _applyScrollRestore(entry) {
    const scrollEl = this._chatHistoryScrollEl();
    if (!scrollEl || !entry) return;

    const prevOverflow = scrollEl.style.overflow;
    scrollEl.style.overflow = 'hidden';
    this._suppressScrollPositionSave = true;

    if (entry.atBottom) {
      scrollEl.scrollTop = scrollEl.scrollHeight;
      this._scrollStuckToBottom = true;
    } else if (!this._applyScrollAnchor(entry)) {
      if (Number.isFinite(entry.scrollTop)) {
        scrollEl.scrollTop = entry.scrollTop;
      }
    } else {
      this._scrollStuckToBottom = false;
    }

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        scrollEl.style.overflow = prevOverflow;
        this._suppressScrollPositionSave = false;
        this._onChatScroll();
        if (!entry.atBottom) {
          this._settleScrollAfterImages(entry);
        }
      });
    });
  }

  _restoreScrollPosition(convId) {
    const entry = this._getScrollPositionEntry(convId);
    if (!entry || entry.atBottom) {
      this.scrollToBottom(true);
      return;
    }
    this._applyScrollRestore(entry);
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
            this.populateConvPresetSelect(patch.preset_id);
          }
        }
      }

      await this.savePresetPromptIfDirty({ silent: true });

      this.applyFontSize();
      this._saveModelOverride();
      this._saveIntegrationUrls();
      await this.loadLlmModelInfo();
      await this.applySdModelSelection({ showStatus: false });
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

  showError(msg, autoHideMs = 8000, options = {}) {
    let text = msg;
    if (options.errorId) {
      text = `${msg} Код для поддержки: ${options.errorId}`;
    }
    this.log?.error('ui', text);
    clearTimeout(this._errorTimer);
    this.$.errorBannerText.textContent = text;
    this.$.errorBannerRetry?.classList.toggle('hidden', !options.showRetry);
    this.$.errorBanner.classList.remove('hidden');
    if (autoHideMs > 0) {
      this._errorTimer = setTimeout(() => this.hideError(), autoHideMs);
    }
  }

  hideError() {
    this.$.errorBanner.classList.add('hidden');
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

    status.classList.remove('hidden');
    this.streamEl.classList.add('waiting', 'is-busy');
    if (opts.stage) {
      status.dataset.stage = opts.stage;
    }
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

  _chatHistoryScrollEl() {
    return this.$.chatHistory?.querySelector('.chat-history-scroll') ?? this.$.chatHistory;
  }

  _distanceFromBottom(el = this._chatHistoryScrollEl()) {
    if (!el) return 0;
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
    const el = this._chatHistoryScrollEl();
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
    const el = this._chatHistoryScrollEl();
    const dist = this._distanceFromBottom(el);
    const show = dist > SCROLL_STICKY_PX;
    this.$.scrollBtn.classList.toggle('visible', show);
    if (show) {
      this.$.scrollBtn.title = this._scrollStuckToBottom
        ? 'Вниз'
        : 'Вниз (следовать за новыми сообщениями)';
    }
  }

  _collectGalleryUrls() {
    const urls = [];
    const seen = new Set();
    const add = (raw) => {
      const resolved = mediaFullUrl(raw);
      if (!resolved || seen.has(resolved)) return;
      seen.add(resolved);
      urls.push(resolved);
    };
    this.$.chatMessages.querySelectorAll('.message-images img').forEach((img) => {
      add(img.dataset.url || img.getAttribute('src'));
    });
    this.$.chatMessages.querySelectorAll('.message-bubble img, .md-inline-img').forEach((img) => {
      add(img.dataset.url || img.getAttribute('src'));
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
    const url = mediaFullUrl(this._lightboxCurrentUrl());
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

  async attachImageUrlToComposer(url, btn = null, { closeLightbox = false } = {}) {
    const resolved = mediaFullUrl(url);
    if (!resolved) return;
    if (!this.currentConvId) {
      this.showError('Сначала выберите или создайте беседу');
      return;
    }
    const key = imageUrlKey(resolved);
    if (this.pendingAttachments.some((a) => imageUrlKey(mediaFullUrl(a.preview_url)) === key)) {
      this.showError('Это изображение уже прикреплено', 3000);
      return;
    }
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(resolved);
      if (!res.ok) throw new Error('Не удалось загрузить изображение');
      const blob = await res.blob();
      const mime = blob.type && blob.type.startsWith('image/') ? blob.type : 'image/png';
      const file = new File([blob], this._filenameFromLightboxUrl(resolved), { type: mime });
      await WebChatComposer.uploadFiles(this, [file]);
      if (closeLightbox) this.closeLightbox();
      this.$.userInput?.focus();
    } catch (err) {
      this.showError(err.message || 'Ошибка прикрепления');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async attachLightboxImageToComposer() {
    const url = this._lightboxCurrentUrl();
    if (!url) return;
    await this.attachImageUrlToComposer(url, this.$.lightboxAttachCurrent, { closeLightbox: true });
  }

  openLightbox(url) {
    const resolved = mediaFullUrl(url);
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
    this.$.lightbox.classList.remove('hidden');
    if (typeof LightboxImage !== 'undefined') {
      LightboxImage.load({
        lightbox: this.$.lightbox,
        img: this.$.lightboxImg,
        loader: this.$.lightboxLoader,
        url,
      });
    } else {
      this.$.lightboxImg.src = url;
    }
    this.$.lightboxImg.alt = `Изображение ${this._lightboxIndex + 1} из ${this._lightboxUrls.length}`;
    this._updateLightboxNav();
    this._updateLightboxActions();
  }

  _updateLightboxActions() {
    if (this.$.lightboxAttachCurrent) {
      this.$.lightboxAttachCurrent.disabled = !this.currentConvId;
    }
    if (this.$.lightboxFavorite) {
      const url = this._lightboxCurrentUrl();
      if (url) void this._syncFavoriteVisualByUrl(url, this.$.lightboxFavorite);
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
      const favored = Boolean(data?.is_favorite);
      this._favoriteStateCache.set(key, favored);
      this._setFavoriteButtonState(btn, favored);
    } catch {
      /* ignore */
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

  closeLightbox() {
    this.$.lightbox.classList.add('hidden');
    if (typeof LightboxImage !== 'undefined') {
      LightboxImage.reset(this.$.lightbox, this.$.lightboxImg, this.$.lightboxLoader);
    } else {
      this.$.lightboxImg.src = '';
    }
    this._lightboxUrls = [];
    this._lightboxIndex = 0;
    this._lightboxTouchStart = null;
    if (this.$.lightboxAttachCurrent) this.$.lightboxAttachCurrent.disabled = false;
    if (!this.$.convSidebar.classList.contains('open')) {
      document.body.style.overflow = '';
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
    this._syncTrustedInternalHosts(llm, sd);
  }

  _updateTrustedInternalHint() {
    const el = document.getElementById('trusted-internal-hint');
    if (!el) return;
    if (!this.config?.auth_enabled) {
      el.classList.add('hidden');
      return;
    }
    const n = this.config.trusted_internal_ip_count ?? 0;
    const env = (this.config.trusted_internal_env_hosts || []).filter(Boolean);
    const ui = (this.config.trusted_internal_ui_hosts || []).filter(Boolean);
    const parts = [];
    if (env.length) parts.push(`.env: ${env.join(', ')}`);
    if (ui.length) parts.push(`настройки чата: ${ui.join(', ')}`);
    el.textContent = parts.length
      ? `Доверенные IP (${n}): ${parts.join(' · ')}. LLM/SD получают /media без cookie.`
      : `Доверенные IP (${n}): укажите адреса LLM/SD — хосты подставятся автоматически.`;
    el.classList.remove('hidden');
  }

  async _syncTrustedInternalHosts(llmUrl, sdUrl) {
    if (!this.config?.auth_enabled) return;
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
      this.config = {
        ...this.config,
        trusted_internal_env_hosts: data.env_hosts,
        trusted_internal_ui_hosts: data.ui_hosts,
        trusted_internal_ip_count: data.ip_count,
      };
      this._updateTrustedInternalHint();
      this.log?.debug('settings', 'Доверенные IP синхронизированы', { ip_count: data.ip_count });
    } catch (err) {
      this.log?.warn('settings', 'Не удалось синхронизировать доверенные IP', err?.message);
    }
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
    const llmUrl = this._normalizeServiceUrl(this.$.llmBaseUrlInput?.value);
    const sdUrl = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
    if (llmUrl) payload.llm_base_url = llmUrl;
    if (sdUrl) payload.sd_webui_url = sdUrl;
    const model = this.getActiveLlmModel();
    if (model) payload.model = model;
    const macroMode = this.getMacroContextMode();
    if (macroMode !== 'selected') payload.macro_context = macroMode;
    if (this.getDocumentRagEnabled()) payload.document_rag = true;
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

  async loadSdModelInfo() {
    const select = this.$.sdModelSelect;
    if (!select) return;
    const refreshBtn = this.$.sdModelRefreshBtn;
    if (refreshBtn) refreshBtn.disabled = true;
    select.innerHTML = '<option value="">Загрузка списка моделей…</option>';
    select.disabled = true;
    try {
      const base = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
      const qs = base ? `?sd_webui_url=${encodeURIComponent(base)}` : '';
      const info = await this.api(`/api/config/sd-models${qs}`);
      this._sdModels = Array.isArray(info?.models) ? info.models : [];
      this._sdSelectedServer = String(info?.selected || '');
      this._syncSdModelSelectState();
    } catch (err) {
      this._sdModels = [];
      this._sdSelectedServer = '';
      select.innerHTML = '<option value="">Не удалось загрузить модели</option>';
      select.disabled = true;
      this.log?.warn('settings', 'Не удалось получить список SD моделей', err?.message || err);
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }

  _syncSdModelSelectState() {
    const select = this.$.sdModelSelect;
    if (!select) return;
    const models = this._sdModels || [];
    if (!models.length) {
      select.innerHTML = '<option value="">Список моделей пуст</option>';
      select.disabled = true;
      return;
    }
    const serverSelected = (this._sdSelectedServer || '').trim();
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

  async applySdModelSelection({ showStatus = true } = {}) {
    const select = this.$.sdModelSelect;
    if (!select || select.disabled) return;
    const title = (select.value || '').trim();
    if (!title) {
      localStorage.removeItem('webchat_sd_model_checkpoint');
      return;
    }
    if (title === (this._sdSelectedServer || '').trim()) {
      localStorage.setItem('webchat_sd_model_checkpoint', title);
      return;
    }
    const prev = (this._sdSelectedServer || '').trim();
    const sdUrl = this._normalizeServiceUrl(this.$.sdWebuiUrlInput?.value);
    select.disabled = true;
    if (showStatus) this._showSettingsSaveStatus('info', 'Применяем SD модель…');
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
      this._sdSelectedServer = title;
      this.log?.info('settings', `SD модель применена: ${title}`);
      if (showStatus) this._showSettingsSaveStatus('success', `SD модель активна: ${title}`);
    } catch (err) {
      if (prev && [...select.options].some((o) => o.value === prev)) {
        select.value = prev;
      }
      if (showStatus) {
        this._showSettingsSaveStatus('error', err?.message || 'Не удалось применить SD модель');
      }
      throw err;
    } finally {
      select.disabled = false;
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
    WebChatComposer.autoResizeInput(this);
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
      const res = await fetch('/api/logs?limit=300', { credentials: 'same-origin' });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = await res.json();
          detail = body.detail || detail;
          if (typeof detail !== 'string') detail = JSON.stringify(detail);
        } catch { /* ignore */ }
        this._serverLogLines = [];
        this.log?.error('app', `Серверный журнал: HTTP ${res.status}`, detail);
        return;
      }
      const data = await res.json();
      this._serverLogLines = data.lines || [];
      this.log?.debug('app', 'Серверный журнал загружен', { lines: this._serverLogLines.length });
    } catch (err) {
      this._serverLogLines = [];
      this.log?.error('app', 'Не удалось загрузить серверный журнал', err);
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
