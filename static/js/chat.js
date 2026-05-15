/**
 * web-chat UI — REST + WebSocket
 */
/* global formatMarkdown */

/** Зона у низа чата: внутри — «прилипание» к автоскроллу. */
const SCROLL_STICKY_PX = 100;

const MESSAGE_STATUS_HTML = `
  <div class="message-status" role="status">
    <div class="status-indicator status-indicator--inline" aria-hidden="true">
      <span class="status-bar"></span><span class="status-bar"></span><span class="status-bar"></span><span class="status-bar"></span>
    </div>
    <span class="message-status-text"></span>
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

const MSG_ICONS = {
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

  sendUserMessage(text, attachmentIds) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Нет соединения с сервером');
    }
    this.ws.send(JSON.stringify({
      type: 'user_message',
      text,
      attachment_ids: attachmentIds,
    }));
  }

  cancel() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cancel' }));
    }
  }

  sendRegenerate(messageId) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('Нет соединения с сервером');
    }
    this.ws.send(JSON.stringify({ type: 'regenerate', message_id: messageId }));
  }

  _dispatch(msg) {
    const h = this.handlers;
    switch (msg.type) {
      case 'connected': h.onConnected?.(msg); break;
      case 'ack': h.onAck?.(msg); break;
      case 'text_delta': h.onTextDelta?.(msg.content || ''); break;
      case 'image': h.onImages?.(msg.urls || []); break;
      case 'tool_start': h.onToolStart?.(msg.name, msg.arguments); break;
      case 'tool_done': h.onToolDone?.(msg.name, msg.summary); break;
      case 'done': h.onDone?.(msg.assistant_message_id); break;
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
    this._inputPlaceholderDefault = 'Сообщение…';
    this._serverLogLines = [];
    this._logsUnsub = null;
    this._pendingDeleteConvId = null;
    this._pendingDeleteBtn = null;
    this._scrollStuckToBottom = true;
    this._lightboxUrls = [];
    this._lightboxIndex = 0;
    this._lightboxTouchStart = null;
    this.log = window.appLog;

    this.$ = {
      backdrop: document.getElementById('sidebar-backdrop'),
      convList: document.getElementById('conv-list'),
      convEmpty: document.getElementById('conv-empty'),
      convSidebar: document.getElementById('conv-sidebar'),
      sidebarSettings: document.getElementById('sidebar-settings'),
      sidebarChatTitle: document.getElementById('sidebar-chat-title'),
      presetSelect: document.getElementById('preset-select'),
      connStatus: document.getElementById('conn-status'),
      connStatusLabel: document.getElementById('conn-status-label'),
      placeholder: document.getElementById('placeholder'),
      chatHistory: document.getElementById('chat-history'),
      chatMessages: document.getElementById('chat-messages'),
      chatFooter: document.getElementById('chat-footer'),
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
      themeToggle: document.getElementById('theme-toggle'),
      logsModal: document.getElementById('logs-modal'),
      logsOutput: document.getElementById('logs-output'),
      logsCount: document.getElementById('logs-count'),
    };

    this.log?.info('app', 'Интерфейс загружен');
    this._bindEvents();
    this._loadTheme();
    this.init();
  }

  async init() {
    try {
      const cfg = await this.api('/api/config');
      this.config = { ...this.config, ...cfg };
    } catch { /* optional */ }

    await Promise.all([this.loadPresets(), this.loadConversations()]);
    const saved = localStorage.getItem('webchat_conv_id');
    if (saved && this.conversations.some((c) => c.id === saved)) {
      await this.selectConversation(saved);
    }
  }

  _bindEvents() {
    document.getElementById('btn-new-chat').addEventListener('click', () => this.openNewConvModal());
    document.getElementById('placeholder-new-chat').addEventListener('click', () => this.openNewConvModal());
    document.getElementById('new-conv-cancel').addEventListener('click', () => this.$.newConvModal.close());
    document.getElementById('new-conv-form').addEventListener('submit', (e) => {
      e.preventDefault();
      this.createConversation();
    });

    document.getElementById('menu-btn').addEventListener('click', () => this.openSidebar());
    document.getElementById('sidebar-close').addEventListener('click', () => this.closeSidebar());
    this.$.backdrop.addEventListener('click', () => this.closeSidebar());

    this.$.themeToggle.addEventListener('click', () => this.toggleTheme());
    document.getElementById('btn-open-logs').addEventListener('click', () => this.openLogsModal());
    document.getElementById('logs-modal-close').addEventListener('click', () => this.closeLogsModal());
    document.getElementById('logs-copy-all').addEventListener('click', () => this.copyAllLogs());
    document.getElementById('logs-clear-all').addEventListener('click', () => this.clearAllLogs());
    this.$.logsModal.addEventListener('close', () => this._stopLogsLiveUpdate());
    document.getElementById('error-banner-close').addEventListener('click', () => this.hideError());
    this.$.sendBtn.addEventListener('click', () => this.sendMessage());
    this.$.cancelBtn.addEventListener('click', () => this.cancelGeneration());

    this.$.userInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    });
    this.$.userInput.addEventListener('input', () => this.autoResizeInput());
    this.$.fileInput.addEventListener('change', (e) => this.uploadFiles(e.target.files));
    this.$.presetSelect.addEventListener('change', () => this.changePreset());
    this.$.chatHistory.addEventListener('scroll', () => this._onChatScroll());
    this.$.scrollBtn.addEventListener('click', () => this.scrollToBottom(true));

    document.getElementById('lightbox-close').addEventListener('click', () => this.closeLightbox());
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
        if (this.$.logsModal.open) {
          this.closeLogsModal();
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

  openSidebar() {
    this.$.convSidebar.classList.add('open');
    this.$.backdrop.classList.remove('hidden');
    requestAnimationFrame(() => this.$.backdrop.classList.add('visible'));
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

  async loadPresets() {
    this.presets = await this.api('/api/presets');
    this.$.newConvPreset.innerHTML = this.presets
      .map((p) => `<option value="${p.id}">${this.escape(p.name)}</option>`)
      .join('');
    const def = this.presets.find((p) => p.is_default);
    if (def) this.$.newConvPreset.value = def.id;
  }

  async loadConversations() {
    this.conversations = await this.api('/api/conversations');
    this.renderConvList();
  }

  renderConvList() {
    this._cancelPendingDelete();
    const empty = !this.conversations.length;
    this.$.convEmpty.classList.toggle('hidden', !empty);
    this.$.convList.classList.toggle('hidden', empty);

    this.$.convList.innerHTML = this.conversations
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
              <div class="conv-item-title">${this.escape(c.title)}</div>
              <div class="conv-item-meta">${date}</div>
            </div>
            <button type="button" class="conv-item-delete" data-id="${c.id}" title="Удалить беседу" aria-label="Удалить беседу">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </li>`;
      })
      .join('');

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
    this.$.sidebarSettings.classList.add('hidden');
    this.$.sidebarChatTitle.textContent = '—';
    this.$.sidebarChatTitle.title = '';
    this.$.placeholder.classList.remove('hidden');
    this.$.chatHistory.classList.add('hidden');
    this.$.chatFooter.classList.add('hidden');
    this.$.chatMessages.innerHTML = '';
    this.$.userInput.value = '';
    this.$.userInput.disabled = true;
    this.$.sendBtn.disabled = true;
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
    if (this.currentConvId === id && this.socket?.ws?.readyState === WebSocket.OPEN) return;

    this._cancelPendingDelete();
    this.disconnectSocket();
    this.log?.info('chat', `Беседа ${id}`);
    this.currentConvId = id;
    localStorage.setItem('webchat_conv_id', id);

    this.$.loadingOverlay.classList.remove('hidden');
    try {
      this.currentConv = await this.api(`/api/conversations/${id}`);
      const preset = this.presets.find((p) => p.id === this.currentConv.preset_id);

    this.$.sidebarChatTitle.textContent = this.currentConv.title;
    this.$.sidebarChatTitle.title = this.currentConv.title;
    this.$.sidebarSettings.classList.remove('hidden');
    this.$.presetSelect.innerHTML = this.presets
        .map((p) => `<option value="${p.id}"${p.id === this.currentConv.preset_id ? ' selected' : ''}>${this.escape(p.name)}</option>`)
        .join('');

      this.$.placeholder.classList.add('hidden');
      this.$.chatHistory.classList.remove('hidden');
      this.$.chatFooter.classList.remove('hidden');
      this.$.userInput.disabled = false;
      this.$.sendBtn.disabled = false;

    await this.loadMessages();
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
    } else if (m.role === 'assistant') {
      this.addAssistantBubble(m.content_text || '', urls, m.id);
    }
  }

  connectSocket() {
    this.setConnStatus('connecting');
    this.log?.info('ws', `Подключение к беседе ${this.currentConvId}`);
    this.socket = new ChatSocket(this.currentConvId, {
      onConnecting: () => this.setConnStatus('connecting'),
      onOpen: () => {
        this.setConnStatus('connected');
        this.log?.info('ws', 'Соединение установлено');
      },
      onClose: () => {
        if (!this.streaming) this.setConnStatus('disconnected');
        this.log?.warn('ws', 'Соединение закрыто');
      },
      onReconnecting: (delay) => {
        this.setConnStatus('connecting');
        this.log?.warn('ws', `Переподключение через ${delay} мс`);
      },
      onError: () => this.log?.error('ws', 'Ошибка WebSocket'),
      onTextDelta: (chunk) => this.onTextDelta(chunk),
      onImages: (urls) => this.onImages(urls),
      onToolStart: (name) => this.onToolStart(name),
      onToolDone: () => this.onToolDone(),
      onAck: (msg) => this.onAck(msg),
      onDone: (msg) => this.onTurnDone(msg.assistant_message_id),
      onWsError: (message, code) => this.onWsError(message, code),
    });
    this.socket.connect();
  }

  disconnectSocket() {
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
      this.socket.sendUserMessage(text, ids);
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
          const bubble = row.querySelector('.chat-message.user');
          if (bubble) bubble.textContent = text;
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
    this.$.userInput.disabled = true;

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
    this.showProgress('Обработка запроса…');
    this.scrollToBottom(true);
  }

  onTextDelta(chunk) {
    if (!this.streamEl) return;
    this.streamText += chunk;
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
    if (!this.streamImagesEl) return;
    if (urls.length) {
      this.hideProgress();
      this.streamEl?.classList.add('has-images');
    }
    for (const url of urls) {
      const resolved = resolveMediaUrl(url);
      if ([...this.streamImagesEl.querySelectorAll('img')].some((i) => i.dataset.url === resolved)) continue;
      const img = document.createElement('img');
      img.src = resolved;
      img.alt = 'Сгенерированное изображение';
      img.dataset.url = resolved;
      img.loading = 'lazy';
      img.addEventListener('click', () => this.openLightbox(resolved));
      img.addEventListener('load', () => this.scrollToBottom(), { once: true });
      this.streamImagesEl.appendChild(img);
    }
    this.scrollToBottom();
  }

  onToolStart(name) {
    const labels = {
      generate_image: 'Генерация изображения…',
      extract_text: 'Извлечение текста из документа…',
    };
    this.showProgress(labels[name] || `Выполняется: ${name}…`);
    this.scrollToBottom();
  }

  onToolDone() {
    if (
      this.streamEl
      && !this.streamText
      && !this.streamImagesEl?.children.length
    ) {
      this.showProgress('Формирую ответ…');
    } else {
      this.hideProgress();
    }
  }

  onTurnDone(assistantMessageId) {
    this.hideProgress();
    if (this.streamRow && assistantMessageId) {
      this.streamRow.dataset.messageId = assistantMessageId;
      this._attachActions(this.streamRow, 'assistant');
    }
    this._regenerating = false;
    this.endStreaming();
    this.loadConversations();
  }

  onWsError(message, code) {
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
    this.streaming = false;
    this.$.sendBtn.classList.remove('hidden', 'loading');
    this.$.sendBtn.disabled = false;
    this.$.cancelBtn.classList.add('hidden');
    this.$.userInput.disabled = false;
    this.$.userInput.focus();

    if (this.streamEl) {
      this.streamEl.classList.remove('streaming', 'waiting', 'has-content', 'has-images');
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
      if (btn.dataset.action === 'delete') this.deleteMessage(id, role);
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
      textEl.textContent = text;
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

    let text = '';
    if (role === 'user') {
      text = row.querySelector('.chat-message.user')?.textContent || '';
      this.$.userInput.placeholder = 'Enter — сохранить и перегенерировать ответ';
    } else {
      text = row.querySelector('.message-bubble')?.innerText || '';
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
      this.socket.sendRegenerate(messageId);
    } catch (err) {
      this.showError(err.message);
      this.endStreaming();
      this._regenerating = false;
    }
  }

  _createImage(url) {
    const img = document.createElement('img');
    img.src = resolveMediaUrl(url);
    img.alt = 'Изображение';
    img.loading = 'lazy';
    img.addEventListener('click', () => this.openLightbox(url));
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

  async changePreset() {
    if (!this.currentConvId) return;
    const presetId = this.$.presetSelect.value;
    this.currentConv = await this.api(`/api/conversations/${this.currentConvId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset_id: presetId }),
    });
    void this.presets.find((p) => p.id === presetId);
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
    this.streamEl.classList.add('waiting');
  }

  hideProgress() {
    const status = this.streamEl?.querySelector('.message-status');
    status?.classList.add('hidden');
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
    if (!this.$.convSidebar.classList.contains('open')) {
      document.body.style.overflow = '';
    }
  }

  autoResizeInput() {
    const ta = this.$.userInput;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
  }

  _loadTheme() {
    const dark = localStorage.getItem('webchat_theme') === 'dark'
      || (!localStorage.getItem('webchat_theme') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.classList.toggle('dark-theme', dark);
  }

  toggleTheme() {
    const dark = !document.body.classList.contains('dark-theme');
    document.body.classList.toggle('dark-theme', dark);
    localStorage.setItem('webchat_theme', dark ? 'dark' : 'light');
  }

  async openLogsModal() {
    await this._fetchServerLogs();
    this._renderLogsView();
    this._stopLogsLiveUpdate();
    this._logsUnsub = this.log?.subscribe(() => {
      if (this.$.logsModal.open) this._renderLogsView();
    }) || null;
    this.$.logsModal.showModal();
    requestAnimationFrame(() => {
      this.$.logsOutput.scrollTop = this.$.logsOutput.scrollHeight;
    });
  }

  closeLogsModal() {
    this.$.logsModal.close();
    this._stopLogsLiveUpdate();
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
      if (this.$.logsModal.open) this._renderLogsView();
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
