/**
 * Рендер истории, пузыри, действия над сообщениями, сетка картинок (P5.4).
 * Подключается до chat.js; API: WebChatMessages.*
 */
(function () {
  'use strict';

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
    const fromAssets = (cj.image_asset_ids || []).map((id) => `/media/asset/${id}`);
    if (fromAssets.length > 0) {
      return [...new Set(fromAssets.map(mediaFullUrl).filter(Boolean))];
    }
    const fromJson = (cj.images || []).filter((u) => {
      const key = imageUrlKey(u);
      return key && !key.includes('/media/generated/');
    });
    const fromParts = m.role === 'user' ? imageUrlsFromParts(cj.parts) : [];
    const hasStructured = fromJson.length > 0 || fromParts.length > 0;
    const fromMd = (
      m.role === 'assistant' && !hasStructured
    ) ? extractMarkdownImageUrls(m.content_text) : [];
    const merged = [...fromJson, ...fromParts, ...fromMd];
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

  function userDisplayText(app, m) {
    const raw = m?.content_text || '';
    if (typeof WebChatImg2imgPreset !== 'undefined') {
      return WebChatImg2imgPreset.stripFromStoredMessage(raw) || raw;
    }
    return raw;
  }

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

  const MSG_IMAGE_ICON_ATTACH =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
  const MSG_IMAGE_ICON_SAVE =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
  const MSG_IMAGE_ICON_DELETE =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
  const MSG_IMAGE_ICON_STAR =
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="12 2 15.1 8.5 22 9.3 17 14.1 18.3 21 12 17.5 5.7 21 7 14.1 2 9.3 8.9 8.5 12 2"/></svg>';
  const MSG_IMAGE_ICON_PROMOTE =
    typeof ICON_PROMOTE_TO_UPLOADS === 'string'
      ? ICON_PROMOTE_TO_UPLOADS
      : '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';

  const MESSAGE_STATUS_HTML = `
  <div class="message-status" role="status" aria-live="polite">
    <div class="message-status-preview-wrap hidden" aria-hidden="true">
      <img class="message-status-preview" alt="" decoding="async" />
    </div>
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

  async function load(app, opts = {}) {
    // Perf: при лагах на 80–100+ сообщениях — замер DevTools Performance;
    // virtual list не внедряем без боли; см. BACKLOG Web Worker для markdown.
    const scrollEl = WebChatScroll.chatHistoryScrollEl(app);
    const restoreEntry = opts.restoreScrollEntry || null;
    const wantScrollEnd = restoreEntry
      ? false
      : (
        opts.scrollToEnd === true
        || (opts.scrollToEnd !== false && app._scrollStuckToBottom)
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
      && !app._messagesFingerprint
      && !app.streaming
      && !app._generationResumeActive;
    if (showSkeleton) {
      app._showMessagesSkeleton();
    }

    const messages = await app.api(`/api/conversations/${app.currentConvId}/messages?limit=100`);
    app._messagesLoading = false;
    app.$.chatMessages?.classList.remove('is-loading');
    dedupeMessageRows(app, {
      preferMessageId: opts.activeStreamingId || app.streamRow?.dataset?.messageId || null,
    });
    const mustRenderDom = showSkeleton || app._isMessagesSkeletonVisible();
    const listFp = app._messagesFingerprintFromList(messages);
    const structureKey = app._messagesStructureKey(messages);
    const activeStreamingId = opts.activeStreamingId
      || app._activeStreamingIdFromList(messages);

    if (!opts.force && listFp === app._messagesFingerprint && !mustRenderDom) {
      if (!hasDuplicateMessageIds(app)) return;
    }

    const domIds = app._domMessageIds();
    const serverIds = messages.map((m) => m.id);
    const domHasDupes = domIds.length !== new Set(domIds).size;
    const sameStructure = !domHasDupes && domIds.join('|') === serverIds.join('|');

    const afterLayout = () => {
      if (restoreEntry) {
        WebChatScroll.applyRestore(app, restoreEntry);
      } else if (preserve && scrollEl) {
        const delta = scrollEl.scrollHeight - scrollHeightBefore;
        scrollEl.scrollTop = scrollTopBefore + delta;
        app._onChatScroll();
      } else {
        app.scrollToBottom(opts.scrollToEnd !== false);
      }
    };

    if (sameStructure && !opts.force) {
      let changed = false;
      let domComplete = domIds.length === serverIds.length;
      for (const m of messages) {
        if (!findRow(app, m.id)) {
          domComplete = false;
          break;
        }
        if (app._patchMessageRowIfNeeded(m, { activeStreamingId })) changed = true;
      }
      if (domComplete && !mustRenderDom) {
        dedupeMessageRows(app, { preferMessageId: activeStreamingId });
        app.$.chatMessages.dataset.structureKey = structureKey;
        app._messagesFingerprint = listFp;
        if (changed || restoreEntry) afterLayout();
        return;
      }
    }

    const canAppendOnly = domIds.length > 0
      && serverIds.length >= domIds.length
      && serverIds.slice(0, domIds.length).join('|') === domIds.join('|');

    if (canAppendOnly && !opts.force) {
      for (let i = domIds.length; i < messages.length; i += 1) {
        const m = messages[i];
        if (findRow(app, m.id)) continue;
        if (m.role === 'assistant' && m.id) {
          app.$.chatMessages
            .querySelectorAll('.message-row.assistant[data-temp="true"]')
            .forEach((row) => row.remove());
        }
        app.$.chatMessages.appendChild(
          app._tagMessageRow(
            rowFromDb(app, m, { activeStreamingId }),
            m,
          ),
        );
      }
      dedupeMessageRows(app, { preferMessageId: activeStreamingId });
      app.$.chatMessages.dataset.structureKey = structureKey;
      app._messagesFingerprint = listFp;
      afterLayout();
      return;
    }

    const fragment = document.createDocumentFragment();
    for (const m of messages) {
      fragment.appendChild(
        app._tagMessageRow(
          rowFromDb(app, m, { activeStreamingId }),
          m,
        ),
      );
    }
    app.$.chatMessages.replaceChildren(fragment);
    dedupeMessageRows(app, { preferMessageId: activeStreamingId });
    app.$.chatMessages.dataset.structureKey = structureKey;
    app._messagesFingerprint = listFp;
    afterLayout();
  }

  function appendFromDb(app, m) {
    app.$.chatMessages.appendChild(rowFromDb(app, m));
  }

  function rowFromDb(app, m, { activeStreamingId = null } = {}) {
    const urls = imageUrlsFromMessage(m);
    if (m.role === 'user') {
      return buildUserRow(app, userDisplayText(app, m), m.id, urls);
    }
    const ragHits = m.content_json?.rag_sources;
    const reasoning = m.content_json?.reasoning;
    const isStreamingDraft = Boolean(m.content_json?.streaming)
      || (activeStreamingId && m.id === activeStreamingId);
    if (m.role === 'assistant' && isStreamingDraft) {
      if (activeStreamingId && m.id !== activeStreamingId) {
        return buildAssistantRow(app, m.content_text || '', urls, m.id, ragHits, reasoning);
      }
      return buildAssistantDraftRow(app, m, urls);
    }
    if (m.role === 'assistant') {
      return buildAssistantRow(app, m.content_text || '', urls, m.id, ragHits, reasoning);
    }
    const fallback = document.createElement('div');
    fallback.className = 'message-row';
    return fallback;
  }

  /** Классы has-content / has-images / has-reasoning — для ширины пузыря и сетки картинок. */

  function syncAssistantLayoutClasses(app, el) {
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

  function ensureAssistantStreamShell(app, el) {
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

  function fillAssistantBubble(app, el, text, imageUrls, reasoning = null) {
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
      if (typeof enhanceCodeCopyButtons === 'function') enhanceCodeCopyButtons(bubble);
    } else if (bubble) {
      bubble.innerHTML = '';
    }
    if (grid) {
      setGridImages(app, grid, imageUrls || []);
    }
    syncAssistantLayoutClasses(app, el);
  }

  function appendAssistantDraftRow(app, m, imageUrls = null) {
    app.$.chatMessages.appendChild(buildAssistantDraftRow(app, m, imageUrls));
  }

  function buildAssistantDraftRow(app, m, imageUrls = null) {
    const urls = imageUrls ?? imageUrlsFromMessage(m);
    const el = document.createElement('div');
    el.className = 'chat-message assistant';
    ensureAssistantStreamShell(app, el);
    fillAssistantBubble(app, el, m.content_text || '', urls, m.content_json?.reasoning);
    const row = wrapMessage(app, 'assistant', el, m.id);
    row.dataset.streamingDraft = 'true';
    return row;
  }

  function findRows(app, messageId) {
    if (!messageId || !app.$.chatMessages) return [];
    return [
      ...app.$.chatMessages.querySelectorAll(
        `.message-row[data-message-id="${CSS.escape(messageId)}"]`,
      ),
    ];
  }

  function findRow(app, messageId) {
    const rows = findRows(app, messageId);
    if (rows.length <= 1) return rows[0] || null;
    return pickMessageRowToKeep(app, rows, messageId);
  }

  function hasDuplicateMessageIds(app) {
    const ids = app.$.chatMessages
      ? [...app.$.chatMessages.querySelectorAll('.message-row[data-message-id]')]
        .map((row) => row.dataset.messageId)
        .filter(Boolean)
      : [];
    return ids.length !== new Set(ids).size;
  }

  function scoreMessageRowKeep(app, row) {
    let score = 0;
    if (row === app.streamRow) score += 200;
    if (row.dataset.streamingDraft === 'true') score -= 40;
    if (row.dataset.contentFp) score += 30;
    const el = row.querySelector('.chat-message.assistant, .chat-message.user');
    if (el?.classList.contains('streaming')) score += 80;
    if (el?.classList.contains('waiting') || el?.classList.contains('is-busy')) score += 20;
    if (el?.querySelector('.message-status[data-stage]')) score += 15;
    const bubbleText = el?.querySelector('.message-bubble')?.textContent?.trim();
    if (bubbleText) score += 10;
    const images = el?.querySelector('.message-images')?.children?.length || 0;
    if (images > 0) score += 10;
    return score;
  }

  function pickMessageRowToKeep(app, rows, messageId = null) {
    if (!rows?.length) return null;
    let best = rows[0];
    let bestScore = scoreMessageRowKeep(app, best);
    for (let i = 1; i < rows.length; i += 1) {
      const row = rows[i];
      const score = scoreMessageRowKeep(app, row);
      if (score > bestScore) {
        best = row;
        bestScore = score;
      }
    }
    return best;
  }

  /** Удаляет дубликаты .message-row с одним data-message-id (оставляет активный stream). */
  function dedupeMessageRows(app, { preferMessageId = null } = {}) {
    const container = app.$.chatMessages;
    if (!container) return;

    const byId = new Map();
    for (const row of container.querySelectorAll('.message-row[data-message-id]')) {
      const id = row.dataset.messageId;
      if (!id) continue;
      if (!byId.has(id)) byId.set(id, []);
      byId.get(id).push(row);
    }

    for (const [id, rows] of byId) {
      if (rows.length <= 1) continue;
      const keep = (preferMessageId === id && app.streamRow && rows.includes(app.streamRow))
        ? app.streamRow
        : pickMessageRowToKeep(app, rows, id);
      for (const row of rows) {
        if (row !== keep) row.remove();
      }
    }

    if (app.streamRow && !container.contains(app.streamRow) && preferMessageId) {
      const kept = findRow(app, preferMessageId);
      if (kept && typeof app._applyStreamUI === 'function') {
        app._applyStreamUI(kept);
      }
    }
  }

  function removeFollowingRows(app, fromRow, includeFrom) {
    let node = includeFrom ? fromRow : fromRow?.nextElementSibling;
    while (node) {
      const next = node.nextElementSibling;
      node.remove();
      node = next;
    }
  }

  function buildMessageActions(app, role, row) {
    const wrap = document.createElement('div');
    wrap.className = 'message-actions';
    wrap.setAttribute('role', 'toolbar');
    wrap.setAttribute('aria-label', 'Действия с сообщением');

    for (const key of messageActionKeysForRow(app, role, row)) {
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
      if (action === 'copy') copyMessageText(app, id, role, btn);
      else if (action === 'delete') onMessageDeleteClick(app, id, role, btn);
      else if (action === 'edit') editMessage(app, id, role);
      else if (action === 'regenerate') regenerateMessage(app, id);
    });

    return wrap;
  }

  function messageActionKeysForRow(app, role, row) {
    const keys = [];
    if (hasCopyableMessageText(app, row, role)) keys.push('copy');
    const hasId = Boolean(row?.dataset?.messageId);
    const isActiveStream = app.streaming && row === app.streamRow;
    if (hasId && !isActiveStream) {
      keys.push('edit', 'regenerate');
    }
    keys.push('delete');
    return keys;
  }

  function syncMessageActions(app, row, role) {
    if (!row || !shouldAttachMessageActions(app, row)) return;
    const actions = row.querySelector('.message-actions');
    const keys = messageActionKeysForRow(app, role, row);
    if (!actions) {
      row.appendChild(buildMessageActions(app, role, row));
      return;
    }
    const current = [...actions.querySelectorAll('[data-action]')].map((b) => b.dataset.action);
    if (keys.length === current.length && keys.every((k) => current.includes(k))) return;
    actions.remove();
    row.appendChild(buildMessageActions(app, role, row));
  }

  function syncAllMessageActions(app) {
    app.$.chatMessages?.querySelectorAll('.message-row[data-message-id]').forEach((row) => {
      const role = row.classList.contains('user') ? 'user' : 'assistant';
      syncMessageActions(app, row, role);
    });
  }

  function hasCopyableMessageText(app, row, role) {
    return Boolean(extractMessagePlainText(app, row, role));
  }

  function shouldAttachMessageActions(app, row) {
    if (!row) return false;
    if (row.dataset.streamingDraft === 'true' || row.dataset.temp === 'true') return false;
    if (app.streaming && row === app.streamRow) return false;
    return true;
  }

  function attachActions(app, row, role) {
    if (!shouldAttachMessageActions(app, row)) return;
    syncMessageActions(app, row, role);
  }

  function wrapMessage(app, role, messageEl, messageId) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;
    if (messageId) row.dataset.messageId = messageId;
    const content = document.createElement('div');
    content.className = 'message-content';
    content.appendChild(messageEl);
    row.appendChild(content);
    if (messageId) attachActions(app, row, role);
    return row;
  }

  function addUserBubble(app, text, messageId = null, imageUrls = []) {
    app.$.chatMessages.appendChild(buildUserRow(app, text, messageId, imageUrls));
    app._scheduleScrollToBottom(true);
  }

  function buildUserRow(app, text, messageId = null, imageUrls = []) {
    const el = document.createElement('div');
    el.className = 'chat-message user';
    if (text) {
      const textEl = document.createElement('div');
      textEl.className = 'user-text';
      textEl.dataset.rawText = text;
      app.promptMacros.renderUserText(textEl, text);
      el.appendChild(textEl);
    }
    const urls = [...new Set(imageUrls.map(mediaFullUrl))];
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(createImage(app, url, { scrollOnLoad: false }));
      el.appendChild(grid);
    }
    return wrapMessage(app, 'user', el, messageId);
  }

  function addAssistantBubble(app, text, imageUrls, messageId = null) {
    app.$.chatMessages.appendChild(buildAssistantRow(app, text, imageUrls, messageId));
    app._scheduleScrollToBottom(true);
  }

  function buildAssistantRow(app, text, imageUrls, messageId = null, ragHits = null, reasoning = null) {
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
      if (typeof enhanceCodeCopyButtons === 'function') enhanceCodeCopyButtons(bubble);
      el.appendChild(bubble);
    }
    if (urls.length) {
      const grid = document.createElement('div');
      grid.className = 'message-images';
      for (const url of urls) grid.appendChild(createImage(app, url, { scrollOnLoad: false }));
      el.appendChild(grid);
    }
    const ragBlock = WebChatMessageBlocks.buildMessageRagSources(ragHits);
    if (ragBlock) el.appendChild(ragBlock);
    syncAssistantLayoutClasses(app, el);
    return wrapMessage(app, 'assistant', el, messageId);
  }

  async function attachRagSourcesAfterTurn(app, assistantMessageId) {
    if (!assistantMessageId || !app.currentConvId) return;

    const row = findRow(app, assistantMessageId);
    const msgEl = row?.querySelector('.chat-message.assistant');
    if (!msgEl || msgEl.querySelector('.message-rag-sources')) return;

    let hits;
    try {
      const messages = await app.api(
        `/api/conversations/${app.currentConvId}/messages?limit=50`,
      );
      const target = messages.find((m) => m.id === assistantMessageId);
      hits = target?.content_json?.rag_sources;
    } catch (err) {
      app.log?.warn('rag', err?.message || 'rag sources load failed');
      return;
    }
    if (!hits?.length) return;

    const block = WebChatMessageBlocks.buildMessageRagSources(hits);
    if (block) msgEl.appendChild(block);
  }

  function extractMessagePlainText(app, row, role) {
    if (!row) return '';
    if (role === 'user') {
      const ut = row.querySelector('.user-text');
      if (ut?.dataset.rawText) return ut.dataset.rawText.trim();
      if (!ut) return '';
      const clone = ut.cloneNode(true);
      clone.querySelectorAll('.mention-spoiler-body').forEach((el) => el.remove());
      return clone.textContent.trim();
    }
    const msgEl = row.querySelector('.chat-message.assistant');
    if (msgEl?.dataset.rawContent != null && msgEl.dataset.rawContent !== '') {
      return assistantDisplayText(msgEl.dataset.rawContent);
    }
    return htmlBubbleToCopyText(row.querySelector('.message-bubble'));
  }

  /** Fallback: plain text из HTML-пузыря с сохранением абзацев и переносов. */
  function htmlBubbleToCopyText(bubble) {
    if (!bubble) return '';
    const clone = bubble.cloneNode(true);
    clone.querySelectorAll('img, .code-copy-btn').forEach((el) => el.remove());
    clone.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
    for (const tag of ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'pre', 'blockquote', 'tr']) {
      clone.querySelectorAll(tag).forEach((el) => {
        el.insertAdjacentText('beforebegin', '\n');
        el.insertAdjacentText('afterend', '\n');
      });
    }
    clone.querySelectorAll('li').forEach((li) => {
      li.insertAdjacentText('afterbegin', '- ');
    });
    return (clone.innerText || '')
      .replace(/\r\n/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function extractMessageCopyHtml(row, role) {
    if (!row || role !== 'assistant') return '';
    const bubble = row.querySelector('.message-bubble');
    if (!bubble) return '';
    const clone = bubble.cloneNode(true);
    clone.querySelectorAll('.code-copy-btn').forEach((el) => el.remove());
    return clone.innerHTML.trim();
  }

  async function writeMessageClipboard(payload) {
    const plain = payload.plain || '';
    const html = payload.html || '';
    if (!plain && !html) return false;

    if (plain && html && navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      try {
        await navigator.clipboard.write([
          new ClipboardItem({
            'text/plain': new Blob([plain], { type: 'text/plain;charset=utf-8' }),
            'text/html': new Blob([html], { type: 'text/html;charset=utf-8' }),
          }),
        ]);
        return true;
      } catch {
        /* fallback to text/plain */
      }
    }

    if (!plain) return false;
    try {
      await navigator.clipboard.writeText(plain);
      return true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = plain;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try {
        return document.execCommand('copy');
      } catch {
        return false;
      } finally {
        ta.remove();
      }
    }
  }

  function flashCopySuccess(app, btn) {
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

  async function copyMessageText(app, messageId, role, copyBtn) {
    const row = findRow(app, messageId);
    const plain = extractMessagePlainText(app, row, role);
    if (!plain) {
      app.showError('Нет текста для копирования');
      return;
    }
    const html = extractMessageCopyHtml(row, role);
    const ok = await writeMessageClipboard({ plain, html });
    if (!ok) {
      app.showError('Не удалось скопировать текст');
      return;
    }
    flashCopySuccess(app, copyBtn);
    app.log?.info('msg', `Текст сообщения скопирован (${role})`);
  }

  function onMessageDeleteClick(app, messageId, role, btn) {
    if (app._pendingDeleteMessageId === messageId) {
      void executeDeleteMessage(app, messageId, role);
      return;
    }
    cancelPendingMessageDelete(app);
    WebChatConversations.cancelPendingDelete(app);
    cancelMessageImageDelete(app);
    app._pendingDeleteMessageId = messageId;
    app._pendingDeleteMessageRole = role;
    app._pendingDeleteMessageBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    findRow(app, messageId)?.classList.add('delete-pending');
  }

  function cancelPendingMessageDelete(app) {
    if (!app._pendingDeleteMessageId) return;
    app._pendingDeleteMessageBtn?.classList.remove('delete-armed');
    if (app._pendingDeleteMessageBtn) {
      app._pendingDeleteMessageBtn.title = 'Удалить';
    }
    findRow(app, app._pendingDeleteMessageId)?.classList.remove('delete-pending');
    app._pendingDeleteMessageId = null;
    app._pendingDeleteMessageBtn = null;
    app._pendingDeleteMessageRole = null;
  }

  async function executeDeleteMessage(app, messageId, role) {
    cancelPendingMessageDelete(app);
    const row = findRow(app, messageId);
    if (!row) return;

    if (app.editingMessageId === messageId) {
      app._cancelMessageEdit();
    }

    if (app.streaming) {
      app.socket?.cancel();
    }

    const cascade = role === 'user';
    app.log?.info('msg', `Удаление ${role} ${messageId} cascade=${cascade}`);

    if (cascade) {
      removeFollowingRows(app, row, true);
    } else {
      row.remove();
    }
    app._messagesFingerprint = `opt-del-${Date.now()}`;

    try {
      await app.api(
        `/api/conversations/${app.currentConvId}/messages/${messageId}?cascade=${cascade}`,
        { method: 'DELETE' },
      );
    } catch (err) {
      app.showError(err.message);
      if (app.currentConvId) {
        await load(app, { force: true, preserveScroll: true });
      }
    }
  }

  async function editMessage(app, messageId, role) {
    if (app.streaming) {
      app.showError('Дождитесь окончания генерации');
      return;
    }
    if (!messageId) {
      app.showError('Сообщение ещё не сохранено. Дождитесь подтверждения отправки.');
      return;
    }
    const row = findRow(app, messageId);
    if (!row) return;

    const text = extractMessagePlainText(app, row, role);
    if (role === 'user') {
      app.$.userInput.placeholder = 'Enter — сохранить и перегенерировать ответ';
      await enterUserMessageEdit(app, messageId, row, text);
      return;
    }

    app.$.userInput.placeholder = 'Enter — сохранить изменения';
    app.editingMessageId = messageId;
    app.editingRole = role;
    app.$.userInput.value = text.trim();
    WebChatComposer.autoResizeInput(app);
    app.$.userInput.focus();
  }

  async function enterUserMessageEdit(app, messageId, row, text) {
    app._editComposerBackup = {
      text: app.$.userInput.value,
      attachments: app.pendingAttachments.map((a) => ({ ...a })),
    };
    WebChatComposer.resetUi(app);

    app.editingMessageId = messageId;
    app.editingRole = 'user';
    row.classList.add('is-being-edited');
    row.querySelector('.message-images')?.classList.add('hidden');

    app.$.userInput.value = text.trim();
    WebChatComposer.autoResizeInput(app);
    app.$.userInput.focus();

    try {
      const attachments = await app.api(
        `/api/conversations/${app.currentConvId}/messages/${messageId}/attachments`,
      );
      for (const att of attachments) {
        if (!att?.id) continue;
        if (app.pendingAttachments.some((a) => a.id === att.id)) continue;
        app.pendingAttachments.push(att);
        WebChatComposer.renderAttachmentChip(app, att);
      }
      if (app.pendingAttachments.length) {
        app.$.attachmentStrip.classList.remove('hidden');
      }
    } catch (err) {
      app.showError(err.message || 'Не удалось загрузить вложения');
    }
  }

  async function regenerateMessage(app, messageId) {
    const row = findRow(app, messageId);
    if (!row) return;
    let targetId = messageId;
    if (row.classList.contains('assistant')) {
      const userRow = previousUserMessageRow(app, row);
      if (!userRow?.dataset?.messageId) {
        app.showError?.('Нет сообщения пользователя для перегенерации');
        return;
      }
      targetId = userRow.dataset.messageId;
    }
    await runRegenerate(app, targetId);
  }

  /**
   * Перегенерация ответа на user-сообщение: user остаётся, ответы после него удаляются.
   */

  function previousUserMessageRow(app, row) {
    let el = row?.previousElementSibling;
    while (el) {
      if (el.classList?.contains('message-row') && el.classList.contains('user')) {
        return el;
      }
      el = el.previousElementSibling;
    }
    return null;
  }

  function llmTextForRegenerate(app, userRow) {
    if (!userRow) return null;
    const rawText = extractMessagePlainText(app, userRow, 'user');
    if (typeof WebChatImg2imgPreset === 'undefined') {
      return rawText || null;
    }
    const payloadText = WebChatImg2imgPreset.getPayloadText(app, rawText);
    WebChatImg2imgPreset.logDiagnostics?.(app, 'regenerate', {
      rawLen: rawText.length,
      payloadLen: payloadText.length,
      injected: payloadText !== rawText,
      outPreview: payloadText.slice(0, 160),
    });
    return payloadText || null;
  }

  async function runRegenerate(app, messageId) {
    if (app.streaming || !app.socket || app._preloadingModels) return;
    const row = findRow(app, messageId);
    if (!row) return;

    if (!(await WebChatPreloadModels.ensureBeforeSend(app))) {
      return;
    }

    if (typeof app._ensureSocketReady === 'function' && !(await app._ensureSocketReady(25000))) {
      app.showError?.('Не удалось подключиться к серверу после загрузки моделей.', 5000, { showRetry: true });
      return;
    }

    removeFollowingRows(app, row, false);

    const llmTextOverride = llmTextForRegenerate(app, row);

    app.log?.info('msg', `Перегенерация user ${messageId}`);
    app._regenerating = true;
    app.startStreaming();
    try {
      app.socket.sendRegenerate(
        messageId,
        app.getWsIntegrationPayload(),
        llmTextOverride,
      );
    } catch (err) {
      app.showError(err.message);
      app.endStreaming();
      app._regenerating = false;
    }
  }

  function gridHasImageKey(app, grid, url) {
    const key = imageUrlKey(url);
    if (!key) return true;
    for (const img of grid.querySelectorAll('img')) {
      if (imageUrlKey(img.dataset.url || img.getAttribute('src')) === key) {
        return true;
      }
    }
    return false;
  }

  function setGridImages(app, grid, urls) {
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
      grid.appendChild(createImage(app, resolved));
    }
    return unique.length;
  }

  function appendImagesToGrid(app, grid, urls) {
    let added = 0;
    for (const raw of urls || []) {
      const resolved = mediaFullUrl(raw);
      if (!resolved || gridHasImageKey(grid, resolved)) continue;
      grid.appendChild(createImage(app, resolved));
      added += 1;
    }
    return added;
  }

  /**
   * Синхронизация картинок при resume/F5: не затирать сетку, если WS уже добавил превью.
   */

  function syncStreamImagesFromServer(app, urls) {
    if (!app.streamImagesEl || !urls?.length) return;
    if (app.streamImagesEl.children.length === 0) {
      setGridImages(app.streamImagesEl, urls);
    } else {
      appendImagesToGrid(app.streamImagesEl, urls);
    }
    syncAssistantLayoutClasses(app.streamEl);
  }

  function createImage(app, url, { scrollOnLoad = true } = {}) {
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
    img.addEventListener('error', () => {
      if (img.src !== full) img.src = full;
    }, { once: true });
    if (scrollOnLoad) {
      img.addEventListener('load', () => app._scheduleScrollToBottom(), { once: true });
    }
    frame.appendChild(img);

    const mediaKey = target ? `${target.source}:${target.id}` : '';
    const deleteBtn = target
      ? `<button type="button" class="gallery-card-action gallery-card-delete danger message-image-delete" data-media-key="${escapeAttr(mediaKey)}" title="Удалить" aria-label="Удалить">${MSG_IMAGE_ICON_DELETE}</button>`
      : '';
    const favoriteBtn = target
      ? `<button type="button" class="gallery-card-action message-image-favorite" data-media-key="${escapeAttr(mediaKey)}" data-full-url="${escapeAttr(full)}" title="В избранное" aria-label="В избранное">${MSG_IMAGE_ICON_STAR}</button>`
      : '';
    const promoteBtn = target
      ? `<button type="button" class="gallery-card-action message-image-promote" data-full-url="${escapeAttr(full)}" title="В галерею загрузок" aria-label="В галерею загрузок">${MSG_IMAGE_ICON_PROMOTE}</button>`
      : '';
    frame.insertAdjacentHTML(
      'beforeend',
      `<button type="button" class="gallery-card-action gallery-card-attach gallery-card-attach-tl message-image-attach" data-full-url="${escapeAttr(full)}" title="Прикрепить это изображение к сообщению" aria-label="Прикрепить к сообщению">${MSG_IMAGE_ICON_ATTACH}</button>
      <div class="gallery-card-actions">
        ${favoriteBtn}
        ${promoteBtn}
        <button type="button" class="gallery-card-action gallery-card-save message-image-save" data-full-url="${escapeAttr(full)}" title="Сохранить" aria-label="Сохранить">${MSG_IMAGE_ICON_SAVE}</button>
        ${deleteBtn}
      </div>`,
    );
    if (target) {
      void app._syncFavoriteVisualByUrl(full, frame.querySelector('.message-image-favorite'));
    }
    return frame;
  }

  function bindMessageImageActions(app) {
    if (!app.$.chatMessages || app._messageImageActionsBound) return;
    app._messageImageActionsBound = true;

    app.$.chatMessages.addEventListener('click', (e) => {
      const attachBtn = e.target.closest('.message-image-attach');
      if (attachBtn) {
        e.preventDefault();
        e.stopPropagation();
        void app.attachImageUrlToComposer(attachBtn.dataset.fullUrl, attachBtn);
        return;
      }
      const saveBtn = e.target.closest('.message-image-save');
      if (saveBtn) {
        e.preventDefault();
        e.stopPropagation();
        void saveMessageImage(app, saveBtn.dataset.fullUrl);
        return;
      }
      const favoriteBtn = e.target.closest('.message-image-favorite');
      if (favoriteBtn) {
        e.preventDefault();
        e.stopPropagation();
        void app._toggleFavoriteByUrl(favoriteBtn.dataset.fullUrl, favoriteBtn);
        return;
      }
      const promoteBtn = e.target.closest('.message-image-promote');
      if (promoteBtn) {
        e.preventDefault();
        e.stopPropagation();
        void app._promoteToUploadsByUrl(promoteBtn.dataset.fullUrl, promoteBtn);
        return;
      }
      const deleteBtn = e.target.closest('.message-image-delete');
      if (deleteBtn) {
        e.preventDefault();
        e.stopPropagation();
        const frame = deleteBtn.closest('.message-image-frame');
        if (frame) onMessageImageDeleteClick(app, frame, deleteBtn);
        return;
      }
      const img = e.target.closest('.message-image-frame img');
      if (img) {
        e.preventDefault();
        app.openLightbox(img.dataset.url || mediaFullUrl(img.src));
        return;
      }
      const inlineImg = e.target.closest('.message-bubble img, .md-inline-img');
      if (inlineImg && !inlineImg.closest('.message-image-frame')) {
        e.preventDefault();
        app.openLightbox(inlineImg.dataset.url || mediaFullUrl(inlineImg.src));
      }
    });

    document.addEventListener('click', (e) => {
      if (!app._pendingImageDeleteKey) return;
      if (e.target.closest('.message-image-delete')) return;
      cancelMessageImageDelete(app);
    });
  }

  function cancelMessageImageDelete(app) {
    if (!app._pendingImageDeleteKey) return;
    app._pendingImageDeleteBtn?.classList.remove('delete-armed');
    app.$.chatMessages
      ?.querySelectorAll(`.message-image-frame[data-media-key="${CSS.escape(app._pendingImageDeleteKey)}"]`)
      .forEach((f) => f.classList.remove('delete-pending'));
    if (app._pendingImageDeleteBtn) {
      app._pendingImageDeleteBtn.title = 'Удалить';
    }
    app._pendingImageDeleteKey = null;
    app._pendingImageDeleteBtn = null;
  }

  function onMessageImageDeleteClick(app, frame, btn) {
    const key = frame.dataset.mediaKey;
    if (!key) return;
    if (app._pendingImageDeleteKey === key) {
      void executeMessageImageDelete(app, frame);
      return;
    }
    cancelPendingMessageDelete(app);
    WebChatConversations.cancelPendingDelete(app);
    cancelMessageImageDelete(app);
    app._pendingImageDeleteKey = key;
    app._pendingImageDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    frame.classList.add('delete-pending');
  }

  async function executeMessageImageDelete(app, frame) {
    const key = frame.dataset.mediaKey;
    const full = frame.dataset.fullUrl || '';
    const target = parseMediaGalleryTarget(full);
    if (!target) return;
    cancelMessageImageDelete(app);
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
      app._messagesFingerprint = `opt-img-${Date.now()}`;
      app.log?.info('chat', `Изображение удалено: ${key}`);
    } catch (err) {
      app.showError(err.message || 'Не удалось удалить');
      if (app.currentConvId) {
        await load(app, { preserveScroll: true });
      }
    }
  }

  async function saveMessageImage(app, url) {
    const full = mediaFullUrl(url);
    if (!full) return;
    try {
      await downloadMediaFile(full, WebChatLightbox.filenameFromUrl(full));
    } catch (err) {
      app.showError(err.message || 'Не удалось скачать');
    }
  }


  function bindDelegatedImageClicks(app) {
    bindMessageImageActions(app);
  }

  window.WebChatMessages = {
    MESSAGE_STATUS_HTML,
    assistantDisplayText,
    imageUrlsFromMessage,
    imageUrlKey,
    userDisplayText,
    load,
    appendFromDb,
    rowFromDb,
    findRow,
    findRows,
    hasDuplicateMessageIds,
    dedupeMessageRows,
    removeFollowingRows,
    fillAssistantBubble,
    ensureAssistantStreamShell,
    syncAssistantLayoutClasses,
    buildAssistantDraftRow,
    appendAssistantDraftRow,
    buildUserRow,
    buildAssistantRow,
    addUserBubble,
    addAssistantBubble,
    setGridImages,
    appendImagesToGrid,
    syncStreamImagesFromServer,
    createImage,
    attachActions,
    syncMessageActions,
    syncAllMessageActions,
    cancelPendingMessageDelete,
    cancelMessageImageDelete,
    attachRagSourcesAfterTurn,
    copyMessageText,
    editMessage,
    regenerateMessage,
    runRegenerate,
    bindMessageImageActions,
    bindDelegatedImageClicks,
  };
})();