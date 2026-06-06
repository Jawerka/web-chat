/**
 * Sidebar: список бесед, корзина, поиск, создание/удаление (P5.4).
 * Подключается до chat.js; API: WebChatConversations.*
 */
(function () {
  'use strict';

  async function load(app) {
    app.conversations = await app.api('/api/conversations');
    app._conversationsFingerprint = fingerprintFrom(app.conversations);
    renderList(app);
  }

  async function loadTrash(app) {
    try {
      app.trashConversations = await app.api('/api/conversations/trash');
      renderTrashList(app);
      updateTrashBadge(app);
    } catch (err) {
      app.log?.warn('trash', err.message);
      app.trashConversations = [];
      renderTrashList(app);
      updateTrashBadge(app);
    }
  }

  function updateTrashBadge(app) {
    const n = app.trashConversations.length;
    if (!app.$.convTrashCount) return;
    app.$.convTrashCount.textContent = String(n);
    app.$.convTrashCount.classList.toggle('hidden', n === 0);
    if (app.$.convTrashEmpty) {
      app.$.convTrashEmpty.classList.toggle('hidden', n > 0);
    }
    if (app.$.convTrashEmptyAll) {
      app.$.convTrashEmptyAll.classList.toggle('hidden', n === 0);
      if (n === 0) {
        cancelPendingEmptyTrash(app);
      }
    }
    app.$.convTrashTabBtn?.classList.toggle('has-items', n > 0);
  }

  function setSidebarTab(app, tab) {
    const showTrash = tab === 'trash';
    app._trashOpen = showTrash;
    app.$.convTrashPanel?.classList.toggle('hidden', !showTrash);
    app.$.convTrashTabBtn?.classList.toggle('is-active', showTrash);
    app.$.convTrashTabBtn?.setAttribute('aria-pressed', showTrash ? 'true' : 'false');
    app.$.convSidebarSheet?.classList.toggle('trash-tab-open', showTrash);
    if (showTrash) {
      closeSearchPanel(app);
      void loadTrash(app);
    }
  }

  function toggleTrashPanel(app) {
    setSidebarTab(app._trashOpen ? 'conversations' : 'trash');
  }

  function cancelPendingEmptyTrash(app) {
    if (!app._pendingEmptyTrash) return;
    app._pendingEmptyTrash = false;
    app.$.convTrashEmptyAll?.classList.remove('delete-armed');
    if (app.$.convTrashEmptyAll) {
      app.$.convTrashEmptyAll.title = 'Удалить все беседы из корзины навсегда';
    }
  }

  function onEmptyTrashClick(app) {
    if (!app.trashConversations.length) return;
    if (app._pendingEmptyTrash) {
      void executeEmptyTrash(app);
      return;
    }
    cancelPendingTrashDelete(app);
    cancelPendingDelete(app);
    app._cancelPendingMessageDelete();
    app._cancelMessageImageDelete();
    app._pendingEmptyTrash = true;
    app.$.convTrashEmptyAll?.classList.add('delete-armed');
    if (app.$.convTrashEmptyAll) {
      app.$.convTrashEmptyAll.title = 'Нажмите ещё раз — удалить всё навсегда';
    }
  }

  async function executeEmptyTrash(app) {
    cancelPendingEmptyTrash(app);
    const snapshot = [...app.trashConversations];
    app.trashConversations = [];
    renderTrashList(app);
    try {
      const result = await app.api('/api/conversations/trash', { method: 'DELETE' });
      const n = result?.deleted ?? snapshot.length;
      app.log?.info('trash', `Корзина очищена: ${n} бесед`);
    } catch (err) {
      app.trashConversations = snapshot;
      renderTrashList(app);
      app.showError(err.message || 'Не удалось очистить корзину');
    }
  }

  function renderTrashList(app) {
    if (!app.$.convTrashList) return;
    cancelPendingTrashDelete(app);
    const days = app.config?.trash_retention_days || 3;
    const html = app.trashConversations
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
              <div class="conv-trash-item-title">${escapeHtml(listTitle(c.title))}</div>
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
    app.$.convTrashList.innerHTML = html;
    updateTrashBadge(app);

    app.$.convTrashList.querySelectorAll('.conv-trash-restore').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        void restoreTrashConversation(btn.dataset.id);
      });
    });
    app.$.convTrashList.querySelectorAll('.conv-trash-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onTrashDeleteClick(btn.dataset.id, btn);
      });
    });
  }

  function cancelPendingTrashDelete(app) {
    if (!app._pendingTrashDeleteId) return;
    app._pendingTrashDeleteBtn?.classList.remove('delete-armed');
    if (app._pendingTrashDeleteBtn) {
      app._pendingTrashDeleteBtn.title = 'Удалить навсегда';
    }
    app.$.convTrashList
      ?.querySelector(`.conv-trash-item[data-id="${app._pendingTrashDeleteId}"]`)
      ?.classList.remove('delete-pending');
    app._pendingTrashDeleteId = null;
    app._pendingTrashDeleteBtn = null;
  }

  function onTrashDeleteClick(app, id, btn) {
    if (app._pendingTrashDeleteId === id) {
      void executePermanentTrashDelete(id);
      return;
    }
    cancelPendingEmptyTrash(app);
    cancelPendingTrashDelete(app);
    cancelPendingDelete(app);
    app._cancelPendingMessageDelete();
    app._cancelMessageImageDelete();
    app._pendingTrashDeleteId = id;
    app._pendingTrashDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.title = 'Нажмите ещё раз для удаления';
    app.$.convTrashList
      ?.querySelector(`.conv-trash-item[data-id="${id}"]`)
      ?.classList.add('delete-pending');
  }

  async function executePermanentTrashDelete(app, id) {
    cancelPendingTrashDelete(app);
    const idx = app.trashConversations.findIndex((c) => c.id === id);
    const removed = idx >= 0 ? app.trashConversations[idx] : null;
    if (idx >= 0) {
      app.trashConversations.splice(idx, 1);
      renderTrashList(app);
    }
    try {
      await app.api(`/api/conversations/${id}/permanent`, { method: 'DELETE' });
      app.log?.info('trash', `Беседа ${id} удалена навсегда`);
    } catch (err) {
      if (removed) {
        app.trashConversations.splice(idx, 0, removed);
        renderTrashList(app);
      }
      app.showError(err.message || 'Не удалось удалить');
    }
  }

  async function restoreTrashConversation(app, id) {
    try {
      const conv = await app.api(`/api/conversations/${id}/restore`, { method: 'POST' });
      app.trashConversations = app.trashConversations.filter((c) => c.id !== id);
      upsertInList(app, conv);
      renderList(app);
      renderTrashList(app);
      await app.selectConversation(conv.id, { prefetchedConversation: conv });
      app.log?.info('trash', `Беседа ${id} восстановлена`);
    } catch (err) {
      app.showError(err.message || 'Не удалось восстановить');
    }
  }

  function fingerprintFrom(conversations) {
    return (conversations || [])
      .map((c) => `${c.id}|${c.updated_at}|${c.in_progress ? 1 : 0}|${c.title}`)
      .join(';');
  }

  async function syncFromServer(app) {
    try {
      const list = await app.api('/api/conversations');
      const fp = fingerprintFrom(list);
      if (fp === app._conversationsFingerprint) return;
      app._conversationsFingerprint = fp;
      const prevId = app.currentConvId;
      app.conversations = list;
      if (prevId) {
        const updated = list.find((c) => c.id === prevId);
        if (updated) {
          app.currentConv = { ...app.currentConv, ...updated };
          app._setSettingsChatTitle(updated.title);
        }
      }
      renderList(app);
    } catch (err) {
      app.log?.warn('sync', err.message);
    }
  }

  function listTitle(title) {
    return WebChatConvTitleFormat?.formatConvTitleForList(title) ?? title;
  }

  function renderList(app) {
    cancelPendingDelete(app);
    const empty = !app.conversations.length;
    app.$.convEmpty.classList.toggle('hidden', !empty);

    const newChatRow = app.$.convList.querySelector('.conv-new-item');
    const convItemsHtml = app.conversations
      .map((c) => {
        const active = c.id === app.currentConvId ? ' active' : '';
        const generating = c.in_progress ? ' is-generating' : '';
        const date = WebChatDateTime.formatDateTime(c.updated_at);
        return `<li class="conv-item${active}${generating}" data-id="${c.id}" role="listitem">
          <div class="conv-item-row">
            <div class="conv-item-main">
              <div class="conv-item-title" data-id="${c.id}" aria-label="${escapeAttr(c.title)}">
                <span class="conv-item-title-text">${escapeHtml(listTitle(c.title))}</span>
                <span class="conv-item-title-tooltip" role="tooltip" aria-hidden="true">
                  <span class="conv-item-title-tooltip-body">${escapeHtml(c.title)}</span>
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

    app.$.convList.innerHTML = '';
    if (newChatRow) {
      app.$.convList.appendChild(newChatRow);
    }
    app.$.convList.insertAdjacentHTML('beforeend', convItemsHtml);

    app.$.convList.querySelectorAll('.conv-item').forEach((el) => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.conv-item-delete')) return;
        if (app._inlineTitleConvId) return;
        if (e.target.closest('.conv-item-title') && (e.detail > 1 || app._convTitleEditGuard)) return;
        app.selectConversation(el.dataset.id);
        app.closeSidebar();
      });
    });

    app.$.convList.querySelectorAll('.conv-item-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onDeleteBtnClick(app, btn.dataset.id, btn);
      });
    });

    bindConvTitleInlineEdit(app);
    updateTitleTooltips(app);
  }

  function updateTitleTooltips(app) {
    app.$.convList.querySelectorAll('.conv-item-title').forEach((el) => {
      const textEl = el.querySelector('.conv-item-title-text');
      if (!textEl) return;
      const truncated = textEl.scrollWidth > textEl.clientWidth + 1;
      el.classList.toggle('is-truncated', truncated);
    });
  }

  function bindConvTitleInlineEdit(app) {
    app.$.convList.querySelectorAll('.conv-item-title').forEach((el) => {
      el.addEventListener('dblclick', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const convId = el.dataset.id || el.closest('.conv-item')?.dataset.id;
        if (convId) {
          app._convTitleEditGuard = true;
          clearTimeout(app._convTitleEditGuardTimer);
          app._convTitleEditGuardTimer = setTimeout(() => {
            app._convTitleEditGuard = false;
          }, 400);
          void startInlineTitleEdit(app, convId, el);
        }
      });
    });
  }

  async function startInlineTitleEdit(app, convId, titleEl) {
    if (app._inlineTitleConvId) return;
    const conv = app.conversations.find((c) => c.id === convId);
    if (!conv) return;

    app._inlineTitleConvId = convId;
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
        if (textEl) textEl.textContent = listTitle(next);
        else titleEl.textContent = listTitle(next);
        if (tipBody) tipBody.textContent = next;
        titleEl.setAttribute('aria-label', next);
      } else {
        if (textEl) textEl.textContent = listTitle(prevTitle);
        else titleEl.textContent = listTitle(prevTitle);
        if (tipBody) tipBody.textContent = prevTitle;
        titleEl.setAttribute('aria-label', prevTitle);
      }
      titleEl.classList.remove('hidden');
      input.remove();
      app._inlineTitleConvId = null;
      updateTitleTooltips(app);
      if (save && next !== prevTitle) {
        await patchConversationTitle(app, convId, next);
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

  async function patchConversationTitle(app, convId, title) {
    const conv = app.conversations.find((c) => c.id === convId);
    const prevTitle = conv?.title;
    const prevCurrentTitle = app.currentConvId === convId ? app.currentConv?.title : null;

    if (conv) conv.title = title;
    if (app.currentConvId === convId && app.currentConv) {
      app.currentConv = { ...app.currentConv, title };
      app._setSettingsChatTitle(title);
    }
    app._conversationsFingerprint = fingerprintFrom(app.conversations);
    renderList(app);

    try {
      const updated = await app.api(`/api/conversations/${convId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      if (conv) conv.title = updated.title;
      if (app.currentConvId === convId) {
        app.currentConv = updated;
        app._setSettingsChatTitle(updated.title);
      }
      app._conversationsFingerprint = fingerprintFrom(app.conversations);
      renderList(app);
    } catch (err) {
      if (conv && prevTitle !== undefined) conv.title = prevTitle;
      if (app.currentConvId === convId && app.currentConv && prevCurrentTitle !== null) {
        app.currentConv = { ...app.currentConv, title: prevCurrentTitle };
        app._setSettingsChatTitle(prevCurrentTitle);
      }
      app._conversationsFingerprint = fingerprintFrom(app.conversations);
      renderList(app);
      app.showError(err.message);
    }
  }

  function onConvSearchInput(app) {
    clearTimeout(app._searchDebounceTimer);
    const q = app.$.convSearch?.value?.trim() ?? '';
    if (!q) {
      clearConvSearch(app);
      return;
    }
    app._searchDebounceTimer = setTimeout(() => {
      void runConvSearch(q);
    }, 300);
  }

  function isConvSearchOpen(app) {
    return app.$.convSearchStack?.classList.contains('is-open') ?? false;
  }

  function openConvSearchPanel(app) {
    setSidebarTab('conversations');
    const stack = app.$.convSearchStack;
    if (!stack) return;
    stack.hidden = false;
    stack.setAttribute('aria-hidden', 'false');
    stack.classList.add('is-open');
    app.$.convSearchToggle?.classList.add('is-active');
    app.$.convSearchToggle?.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => app.$.convSearch?.focus());
  }

  function closeSearchPanel(app) {
    const stack = app.$.convSearchStack;
    if (!stack || !isConvSearchOpen(app)) return;
    stack.classList.remove('is-open');
    app.$.convSearchToggle?.classList.remove('is-active');
    app.$.convSearchToggle?.setAttribute('aria-expanded', 'false');
    const hideStack = () => {
      if (!stack.classList.contains('is-open')) {
        stack.hidden = true;
        stack.setAttribute('aria-hidden', 'true');
      }
    };
    stack.addEventListener('transitionend', hideStack, { once: true });
    setTimeout(hideStack, 280);
    clearConvSearch(app);
  }

  function toggleConvSearchPanel(app) {
    if (isConvSearchOpen(app)) {
      closeSearchPanel(app);
    } else {
      openConvSearchPanel(app);
    }
  }

  function clearConvSearch(app) {
    if (app.$.convSearch) app.$.convSearch.value = '';
    app.$.convSearchResults?.classList.add('hidden');
    if (app.$.convSearchResults) app.$.convSearchResults.innerHTML = '';
  }

  function showConvSearchSkeleton(app) {
    const el = app.$.convSearchResults;
    if (!el) return;
    el.classList.remove('hidden');
    el.innerHTML = Array.from({ length: 3 }, () => (
      `<li class="conv-search-skeleton" aria-hidden="true">
        <div class="conv-search-skeleton-line conv-search-skeleton-line--title"></div>
        <div class="conv-search-skeleton-line conv-search-skeleton-line--snippet"></div>
      </li>`
    )).join('');
  }

  async function runConvSearch(app, q) {
    if (!app.$.convSearchResults) return;
    showConvSearchSkeleton(app);
    try {
      const hits = await app.api(`/api/search?q=${encodeURIComponent(q)}`);
      renderSearchResults(hits, q);
    } catch (err) {
      app.showError(err.message);
      clearConvSearch(app);
    }
  }

  function renderSearchResults(app, hits, q) {
    const el = app.$.convSearchResults;
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
          <div class="conv-search-hit-title">${escapeHtml(listTitle(h.conversation_title))}</div>
          <div class="conv-search-hit-meta">
            <span class="conv-search-hit-kind">${kindLabel}</span>
            <div class="conv-search-hit-snippet">${highlightSearchSnippet(h.snippet, q)}</div>
          </div>
        </li>`;
      })
      .join('');

    el.querySelectorAll('.conv-search-hit').forEach((item) => {
      const open = () => {
        void openSearchHit(
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

  function highlightSearchSnippet(snippet, query) {
    let html = escapeHtml(snippet);
    const words = query.trim().split(/\s+/).filter(Boolean);
    for (const word of words) {
      if (!word) continue;
      const re = new RegExp(`(${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
  }

  async function openSearchHit(app, conversationId, messageId) {
    closeSearchPanel(app);
    app.closeSidebar();
    await app.selectConversation(conversationId, { scrollToMessageId: messageId || null });
  }

  function onDeleteBtnClick(app, id, btn) {
    if (app._pendingDeleteConvId === id) {
      void executeDeleteConversation(app, id);
      return;
    }
    app._cancelPendingMessageDelete();
    app._cancelMessageImageDelete();
    cancelPendingEmptyTrash(app);
    cancelPendingTrashDelete(app);
    cancelPendingDelete(app);
    app._pendingDeleteConvId = id;
    btn.title = 'Нажмите ещё раз — в корзину';
    app._pendingDeleteBtn = btn;
    btn.classList.add('delete-armed');
    btn.closest('.conv-item')?.classList.add('delete-pending');
    btn.title = 'Нажмите ещё раз для удаления';
  }

  function cancelPendingDelete(app) {
    if (!app._pendingDeleteConvId) return;
    app._pendingDeleteBtn?.classList.remove('delete-armed');
    app._pendingDeleteBtn?.closest('.conv-item')?.classList.remove('delete-pending');
    if (app._pendingDeleteBtn) {
      app._pendingDeleteBtn.title = 'Удалить в корзину';
    }
    app._pendingDeleteConvId = null;
    app._pendingDeleteBtn = null;
  }

  function snapshotConversationListState(app) {
    return {
      conversations: [...app.conversations],
      fingerprint: app._conversationsFingerprint,
      currentConvId: app.currentConvId,
      currentConv: app.currentConv ? { ...app.currentConv } : null,
    };
  }

  function restoreConversationListState(app, snapshot) {
    app.conversations = snapshot.conversations;
    app._conversationsFingerprint = snapshot.fingerprint;
    renderList(app);
  }

  /**
   * Мгновенно убрать беседу из списка (оптимистично).
   * @returns {{ snapshot, nextConvId: string|null }}
   */

  function optimisticRemoveConversation(app, id) {
    const snapshot = snapshotConversationListState(app);
    const wasCurrent = app.currentConvId === id;
    const remaining = app.conversations.filter((c) => c.id !== id);
    const nextConvId = wasCurrent && remaining.length ? remaining[0].id : null;

    app.conversations = remaining;
    app._conversationsFingerprint = fingerprintFrom(app.conversations);
    WebChatComposer.clearDraft(app, id);
    app._clearScrollPosition(id);

    if (wasCurrent) {
      app.disconnectSocket();
      if (nextConvId) {
        void app.selectConversation(nextConvId);
      } else {
        app._clearCurrentConversation();
      }
    }

    renderList(app);
    return { snapshot, nextConvId, wasCurrent };
  }

  async function executeDeleteConversation(app, id) {
    cancelPendingDelete(app);
    if (app._deletingConvIds.has(id)) return;
    app._deletingConvIds.add(id);

    const { snapshot, wasCurrent } = optimisticRemoveConversation(app, id);
    app.log?.info('chat', `Удаление беседы ${id}`);

    try {
      await app.api(`/api/conversations/${id}`, { method: 'DELETE' });
      void loadTrash(app);
      void syncFromServer(app);
    } catch (err) {
      restoreConversationListState(app, snapshot);
      if (wasCurrent && snapshot.currentConvId === id && snapshot.currentConv) {
        app.currentConvId = id;
        app.currentConv = snapshot.currentConv;
        localStorage.setItem('webchat_conv_id', id);
        void app.selectConversation(id, { prefetchedConversation: snapshot.currentConv });
      }
      app.showError(err.message || 'Не удалось удалить беседу');
    } finally {
      app._deletingConvIds.delete(id);
    }
  }

  function upsertInList(app, conv) {
    const idx = app.conversations.findIndex((c) => c.id === conv.id);
    if (idx >= 0) {
      app.conversations[idx] = { ...app.conversations[idx], ...conv };
    } else {
      app.conversations.unshift(conv);
    }
    app._conversationsFingerprint = fingerprintFrom(app.conversations);
  }

  async function create(app) {
    const title = 'Новая беседа';
    const presetId = WebChatPresets.getLastUsedPresetId(app);
    const body = { title };
    if (presetId) body.preset_id = presetId;
    const conv = await app.api('/api/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    upsertInList(app, conv);
    await app.selectConversation(conv.id, { prefetchedConversation: conv });
    void load(app);
  }

  function bindSidebarEvents(app) {
    const onNewChat = () => void create(app);
    document.getElementById('btn-new-chat')?.addEventListener('click', onNewChat);
    app.$.convTrashTabBtn?.addEventListener('click', () => toggleTrashPanel(app));
    app.$.convTrashEmptyAll?.addEventListener('click', () => onEmptyTrashClick(app));
    document.getElementById('placeholder-new-chat')?.addEventListener('click', onNewChat);
    app.$.convSearchToggle?.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleConvSearchPanel(app);
    });
    app.$.convSearchClose?.addEventListener('click', (e) => {
      e.stopPropagation();
      closeSearchPanel(app);
    });
    app.$.convSearch?.addEventListener('input', () => onConvSearchInput(app));
    app.$.convSearch?.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeSearchPanel(app);
      }
    });
    app._convSearchOutsideClick = (e) => {
      if (!isConvSearchOpen(app)) return;
      const t = e.target;
      if (app.$.convSearchStack?.contains(t) || app.$.convSearchToggle?.contains(t)) return;
      closeSearchPanel(app);
    };
    document.addEventListener('mousedown', app._convSearchOutsideClick);
  }

  window.WebChatConversations = {
    load,
    loadTrash,
    renderList,
    fingerprintFrom,
    syncFromServer,
    setSidebarTab,
    toggleTrashPanel,
    closeSearchPanel,
    cancelPendingDelete,
    upsertInList,
    create,
    updateTitleTooltips,
    bindSidebarEvents,
    patchConversationTitle,
  };
})();