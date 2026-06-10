/**
 * Composer: ввод, вложения, черновики, send/stop UI, tools menu.
 * Подключается до chat.js; API: WebChatComposer.*
 */
(function () {
  'use strict';

  const COMPOSER_DRAFTS_STORAGE_KEY = 'webchat_composer_drafts_v1';
  const PENDING_ATTACHMENTS_KEY = 'webchat_pending_attachments';
  const ACCEPTED_UPLOAD_ACCEPT =
    'image/jpeg,image/png,image/webp,image/gif,image/*,application/pdf,.docx,text/plain,text/csv';

  function readComposerDrafts() {
    try {
      const raw = localStorage.getItem(COMPOSER_DRAFTS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  function writeComposerDrafts(app, drafts) {
    try {
      localStorage.setItem(COMPOSER_DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
    } catch (err) {
      app.log?.warn('chat', `Не удалось сохранить черновик: ${err.message}`);
    }
  }

  function hasPayload(app, text) {
    const trimmed = (text ?? app.$.userInput?.value ?? '').trim();
    return Boolean(trimmed || app.pendingAttachments.length);
  }

  function isBusy(app) {
    return Boolean(app.streaming || app._generationResumeActive || app._preloadingModels);
  }

  function syncSendState(app) {
    const sendBtn = app.$.sendBtn;
    const cancelBtn = app.$.cancelBtn;
    if (!sendBtn) return;

    if (isBusy(app)) {
      sendBtn.classList.add('hidden');
      sendBtn.disabled = true;
      cancelBtn?.classList.remove('hidden');
      return;
    }

    cancelBtn?.classList.add('hidden');
    sendBtn.classList.remove('hidden');
    const inputDisabled = Boolean(app.$.userInput?.disabled);
    sendBtn.disabled = !app.currentConvId || inputDisabled;
  }

  function sendBlockedReason(app, text) {
    if (!app.currentConvId) return 'Сначала выберите или создайте беседу';
    if (app.$.userInput?.disabled) return 'Поле ввода недоступно';
    if (app._preloadingModels) return 'Загрузка моделей…';
    if (isBusy(app)) return 'Дождитесь окончания генерации';
    if (!hasPayload(app, text)) return null;
    if (!app.socket) return 'Подключение к серверу…';
    if (app.socket.ws?.readyState === WebSocket.CONNECTING) return 'Подключение к серверу…';
    if (app.socket.ws?.readyState !== WebSocket.OPEN) return 'Нет соединения с сервером';
    return null;
  }

  function scheduleDraftSave(app) {
    if (!app.currentConvId) return;
    clearTimeout(app._composerDraftDebounceTimer);
    app._composerDraftDebounceTimer = setTimeout(
      () => saveDraft(app, app.currentConvId),
      400,
    );
  }

  function saveDraft(app, convId = app.currentConvId) {
    if (!convId) return;
    const text = app.$.userInput?.value ?? '';
    const attachments = app.pendingAttachments.map((a) => ({
      id: a.id,
      original_name: a.original_name,
      mime_type: a.mime_type,
      size_bytes: a.size_bytes,
      preview_url: a.preview_url,
    }));
    const drafts = readComposerDrafts();
    if (!text.trim() && !attachments.length) {
      delete drafts[convId];
    } else {
      drafts[convId] = { text, attachments, updatedAt: Date.now() };
    }
    writeComposerDrafts(app, drafts);
  }

  function clearDraft(app, convId) {
    if (!convId) return;
    const drafts = readComposerDrafts();
    if (!drafts[convId]) return;
    delete drafts[convId];
    writeComposerDrafts(app, drafts);
  }

  function resetUi(app) {
    app.pendingAttachments = [];
    app.$.attachmentStrip.innerHTML = '';
    app.$.attachmentStrip.classList.add('hidden');
    closeToolsMenu(app);
    if (app.$.userInput) {
      app.$.userInput.value = '';
      autoResizeInput(app);
    }
  }

  function restoreDraft(app, convId) {
    if (!convId) return;
    const draft = readComposerDrafts()[convId];
    if (!draft) return;

    if (typeof draft.text === 'string' && app.$.userInput) {
      app.$.userInput.value = draft.text;
      autoResizeInput(app);
    }

    const list = Array.isArray(draft.attachments) ? draft.attachments : [];
    for (const att of list) {
      if (!att?.id) continue;
      if (app.pendingAttachments.some((a) => a.id === att.id)) continue;
      app.pendingAttachments.push(att);
      renderAttachmentChip(app, att);
    }
    if (app.pendingAttachments.length) {
      app.$.attachmentStrip.classList.remove('hidden');
    }
  }

  function isToolsMenuOpen(app) {
    return app.$.composerToolsMenu?.classList.contains('is-open') ?? false;
  }

  function openToolsMenu(app) {
    const menu = app.$.composerToolsMenu;
    const btn = app.$.composerMoreBtn;
    if (!menu || !btn || btn.disabled) return;
    menu.classList.remove('hidden');
    menu.classList.add('is-open');
    btn.classList.add('is-open');
    btn.setAttribute('aria-expanded', 'true');
  }

  function closeToolsMenu(app) {
    const menu = app.$.composerToolsMenu;
    const btn = app.$.composerMoreBtn;
    if (!menu || menu.classList.contains('hidden')) return;
    menu.classList.add('hidden');
    menu.classList.remove('is-open');
    btn?.classList.remove('is-open');
    btn?.setAttribute('aria-expanded', 'false');
  }

  function toggleToolsMenu(app) {
    if (isToolsMenuOpen(app)) closeToolsMenu(app);
    else openToolsMenu(app);
  }

  function initToolsMenu(app) {
    const btn = app.$.composerMoreBtn;
    const menu = app.$.composerToolsMenu;
    if (!btn || !menu) return;

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleToolsMenu(app);
    });

    menu.querySelectorAll('.composer-tools-menu-item').forEach((item) => {
      item.addEventListener('click', () => {
        requestAnimationFrame(() => closeToolsMenu(app));
      });
    });

    const onDocClick = (e) => {
      if (!isToolsMenuOpen(app)) return;
      const t = e.target;
      if (btn.contains(t) || menu.contains(t)) return;
      closeToolsMenu(app);
    };
    const onDocKeydown = (e) => {
      if (e.key === 'Escape') closeToolsMenu(app);
    };
    document.addEventListener('click', onDocClick);
    document.addEventListener('keydown', onDocKeydown);
    app._composerDocListeners = { onDocClick, onDocKeydown };
  }

  function initScrollPadObserver(app) {
    const composer = app.$.chatComposer;
    if (!composer || typeof ResizeObserver === 'undefined') return;
    app._composerResizeObserver?.disconnect();
    app._composerResizeObserver = new ResizeObserver(() => syncScrollPad(app));
    app._composerResizeObserver.observe(composer);
  }

  /** Снять document listeners и ResizeObserver (P5.8). */
  function disconnectComposer(app) {
    const doc = app._composerDocListeners;
    if (doc) {
      document.removeEventListener('click', doc.onDocClick);
      document.removeEventListener('keydown', doc.onDocKeydown);
      app._composerDocListeners = null;
    }
    app._composerResizeObserver?.disconnect();
    app._composerResizeObserver = null;
  }

  function syncScrollPad(app) {
    const composer = app.$.chatComposer;
    if (!composer) return;
    const fadeEl = app.$.chatHistory?.querySelector(':scope > .chat-composer-edge-fade');
    const fadeH = fadeEl?.offsetHeight || 100;
    const pad = Math.max(120, composer.offsetHeight + fadeH + 20);
    document.documentElement.style.setProperty('--composer-scroll-pad', `${pad}px`);
  }

  function chatInputMetrics(app) {
    const ta = app.$.userInput;
    const box = ta?.closest('.composer-input-row') || ta?.closest('.chat-input-container');
    const taStyle = ta ? getComputedStyle(ta) : null;
    const boxStyle = box ? getComputedStyle(box) : null;
    const lineHeight = parseFloat(taStyle?.lineHeight) || 20;
    const padY = parseFloat(taStyle?.paddingTop || 0) + parseFloat(taStyle?.paddingBottom || 0);
    const borderY = parseFloat(taStyle?.borderTopWidth || 0) + parseFloat(taStyle?.borderBottomWidth || 0);
    const maxRows = parseInt(boxStyle?.getPropertyValue('--chat-input-max-rows'), 10) || 10;
    const minH = lineHeight + padY + borderY;
    const maxH = lineHeight * maxRows + padY + borderY;
    return { lineHeight, padY, borderY, maxRows, minH, maxH };
  }

  function autoResizeInput(app) {
    const ta = app.$.userInput;
    if (!ta) return;

    const { lineHeight, padY, borderY, maxRows, minH, maxH } = chatInputMetrics(app);

    if (!ta.value) {
      ta.style.height = `${minH}px`;
      ta.rows = 1;
      ta.classList.remove('chat-input--scrollable');
      syncScrollPad(app);
      return;
    }

    ta.style.height = '0px';
    const contentH = ta.scrollHeight;
    const nextH = Math.min(Math.max(contentH, minH), maxH);
    ta.style.height = `${nextH}px`;

    const rows = Math.min(
      maxRows,
      Math.max(1, Math.ceil((nextH - padY - borderY) / lineHeight)),
    );
    if (ta.rows !== rows) ta.rows = rows;

    const overflow = contentH > maxH + 1;
    ta.classList.toggle('chat-input--scrollable', overflow);
    if (overflow) ta.scrollTop = ta.scrollHeight;
    syncScrollPad(app);
  }

  function dataTransferHasFiles(dt) {
    if (!dt?.types) return false;
    const types = dt.types;
    if (typeof types.includes === 'function') return types.includes('Files');
    return Array.from(types).indexOf('Files') >= 0;
  }

  function filesFromDataTransfer(dt) {
    if (!dt) return [];
    if (dt.files?.length) return Array.from(dt.files);
    const out = [];
    if (dt.items) {
      for (const item of dt.items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) out.push(f);
        }
      }
    }
    return out;
  }

  function filesFromClipboard(clipboardData) {
    if (!clipboardData) return [];
    const fromItems = [];
    if (clipboardData.items) {
      for (const item of clipboardData.items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) fromItems.push(f);
        }
      }
    }
    if (fromItems.length) return fromItems;
    if (clipboardData.files?.length) return Array.from(clipboardData.files);
    return [];
  }

  function setFileDragActive(app, active) {
    app.$.chatBody?.classList.toggle('is-file-drag', active);
    app.$.chatDropOverlay?.classList.toggle('hidden', !active);
    app.$.chatDropOverlay?.setAttribute('aria-hidden', active ? 'false' : 'true');
    const dropZone = document.getElementById('drop-zone');
    dropZone?.classList.toggle('drag-over', active);
    if (active && app.$.chatDropOverlayTitle && !app._uploadInProgress) {
      app.$.chatDropOverlayTitle.textContent = 'Отпустите для прикрепления';
    }
  }

  function setUploading(app, uploading) {
    app._uploadInProgress = uploading;
    app.$.chatDropOverlay?.classList.toggle('is-uploading', uploading);
    if (uploading && app.$.chatDropOverlayTitle) {
      app.$.chatDropOverlayTitle.textContent = 'Загрузка…';
    }
  }

  function onPaste(app, e) {
    const files = filesFromClipboard(e.clipboardData);
    if (files.length) {
      e.preventDefault();
      void uploadFiles(app, files);
      return;
    }
    requestAnimationFrame(() => autoResizeInput(app));
  }

  function initFileHandlers(app) {
    const body = app.$.chatBody;
    const dropZone = document.getElementById('drop-zone');
    if (!body) return;

    const onDragEnter = (e) => {
      if (!dataTransferHasFiles(e.dataTransfer)) return;
      e.preventDefault();
      app._fileDragDepth += 1;
      if (app._fileDragDepth === 1) setFileDragActive(app, true);
    };
    const onDragOver = (e) => {
      if (!dataTransferHasFiles(e.dataTransfer)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
      if (!body.classList.contains('is-file-drag')) setFileDragActive(app, true);
    };
    const onDragLeave = (e) => {
      if (!dataTransferHasFiles(e.dataTransfer)) return;
      const leaving = e.currentTarget;
      const related = e.relatedTarget;
      if (related && leaving.contains(related)) return;
      app._fileDragDepth = Math.max(0, app._fileDragDepth - 1);
      if (app._fileDragDepth === 0) setFileDragActive(app, false);
    };
    const onDrop = (e) => {
      if (!dataTransferHasFiles(e.dataTransfer)) return;
      e.preventDefault();
      app._fileDragDepth = 0;
      setFileDragActive(app, false);
      dropZone?.classList.remove('drag-over');
      const files = filesFromDataTransfer(e.dataTransfer);
      if (files.length) void uploadFiles(app, files);
    };

    body.addEventListener('dragenter', onDragEnter);
    body.addEventListener('dragover', onDragOver);
    body.addEventListener('dragleave', onDragLeave);
    body.addEventListener('drop', onDrop);
    document.addEventListener('dragend', () => {
      app._fileDragDepth = 0;
      setFileDragActive(app, false);
      dropZone?.classList.remove('drag-over');
    });

    if (app.$.fileInput && !app.$.fileInput.accept) {
      app.$.fileInput.accept = ACCEPTED_UPLOAD_ACCEPT;
    }
  }

  async function uploadFiles(app, fileList) {
    if (!app.currentConvId) {
      app.showError('Сначала выберите или создайте беседу');
      return;
    }
    if (!fileList?.length) return;
    const files = Array.from(fileList);
    const max = app.config.max_files_per_message || 10;
    if (app.pendingAttachments.length + files.length > max) {
      app.showError(`Максимум ${max} файлов за сообщение`);
      return;
    }

    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    fd.append('conversation_id', app.currentConvId);

    setUploading(app, true);
    try {
      const res = await fetch('/api/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = formatApiErrorDetail(body.detail);
        throw new Error(detail || res.statusText || 'Ошибка загрузки');
      }
      const data = await res.json();
      const added = data.attachments || [];
      for (const att of added) {
        app.pendingAttachments.push(att);
        renderAttachmentChip(app, att, { isNew: true });
      }
      if (added.length) {
        app.$.attachmentStrip.classList.remove('hidden');
        saveDraft(app, app.currentConvId);
        const names = added.map((a) => a.original_name).filter(Boolean);
        if (added.length === 1) {
          app.showUploadSuccess(`Прикреплён: ${names[0]}`);
        } else {
          app.showUploadSuccess(`Прикреплено файлов: ${added.length}`);
        }
      }
    } catch (err) {
      app.showError(err.message);
    } finally {
      setUploading(app, false);
      setFileDragActive(app, false);
    }
    if (app.$.fileInput) app.$.fileInput.value = '';
  }

  function renderAttachmentChip(app, att, { isNew = false } = {}) {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip' + (isNew ? ' is-new' : '');
    chip.dataset.id = att.id;
    const previewUrl = att.preview_url ? mediaPreviewUrl(att.preview_url) : '';
    const preview = previewUrl
      ? `<img src="${app.escapeAttr(previewUrl)}" alt="">`
      : '<span class="chip-file-icon">📄</span>';
    chip.innerHTML = `${preview}<span class="chip-name">${app.escape(att.original_name)}</span>
      <button type="button" class="attachment-chip-remove" title="Убрать" aria-label="Убрать">×</button>`;
    chip.querySelector('.attachment-chip-remove').addEventListener('click', () => {
      app.pendingAttachments = app.pendingAttachments.filter((a) => a.id !== att.id);
      chip.remove();
      if (!app.pendingAttachments.length) {
        app.$.attachmentStrip.classList.add('hidden');
      }
      saveDraft(app, app.currentConvId);
    });
    app.$.attachmentStrip.appendChild(chip);
  }

  function clearAttachments(app) {
    app.pendingAttachments = [];
    app.$.attachmentStrip.innerHTML = '';
    app.$.attachmentStrip.classList.add('hidden');
  }

  function restorePendingFromSession(app) {
    const raw = sessionStorage.getItem(PENDING_ATTACHMENTS_KEY);
    if (!raw) return;
    sessionStorage.removeItem(PENDING_ATTACHMENTS_KEY);
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (payload.conversation_id && payload.conversation_id !== app.currentConvId) return;
    const list = payload.attachments;
    if (!Array.isArray(list) || !list.length) return;
    for (const att of list) {
      if (!att?.id) continue;
      if (app.pendingAttachments.some((a) => a.id === att.id)) continue;
      app.pendingAttachments.push(att);
      renderAttachmentChip(app, att);
    }
    if (typeof payload.composer_text === 'string' && payload.composer_text.trim() && app.$.userInput) {
      app.$.userInput.value = payload.composer_text;
      autoResizeInput(app);
    }

    if (app.pendingAttachments.length) {
      app.$.attachmentStrip.classList.remove('hidden');
    }
    if (app.pendingAttachments.length || (app.$.userInput?.value || '').trim()) {
      app.$.userInput?.focus();
    }
    saveDraft(app, app.currentConvId);
  }

  function bindEvents(app) {
    app.$.sendBtn?.addEventListener('click', () => app.sendMessage());
    app.$.userInput?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        if (app.promptMacros.isAutocompleteOpen()) {
          e.preventDefault();
          app.promptMacros.applyAutocompleteSelection();
          return;
        }
        e.preventDefault();
        app.sendMessage();
      }
    });
    app.$.userInput?.addEventListener('input', () => {
      autoResizeInput(app);
      scheduleDraftSave(app);
      app._scheduleRagPreview();
    });
    app.$.userInput?.addEventListener('paste', (e) => onPaste(app, e));
    window.addEventListener('resize', () => autoResizeInput(app));
    requestAnimationFrame(() => {
      autoResizeInput(app);
      syncScrollPad(app);
    });
    initScrollPadObserver(app);
    app.$.fileInput?.addEventListener('change', (e) => uploadFiles(app, e.target.files));
    initToolsMenu(app);
  }

  window.WebChatComposer = {
    COMPOSER_DRAFTS_STORAGE_KEY,
    PENDING_ATTACHMENTS_KEY,
    ACCEPTED_UPLOAD_ACCEPT,
    hasPayload,
    isBusy,
    syncSendState,
    sendBlockedReason,
    scheduleDraftSave,
    saveDraft,
    clearDraft,
    restoreDraft,
    resetUi,
    initFileHandlers,
    onPaste,
    uploadFiles,
    renderAttachmentChip,
    clearAttachments,
    restorePendingFromSession,
    initScrollPadObserver,
    disconnectComposer,
    syncScrollPad,
    autoResizeInput,
    closeToolsMenu,
    initToolsMenu,
    bindEvents,
  };
})();
