/**
 * Позиция прокрутки истории по беседе (localStorage).
 * Подключается до chat.js; API: WebChatScroll.*
 */
(function () {
  'use strict';

  const SCROLL_STICKY_PX = 72;
  const SCROLL_POSITIONS_STORAGE_KEY = 'webchat_scroll_positions_v1';
  const SCROLL_POSITION_SAVE_DEBOUNCE_MS = 400;
  const SCROLL_POSITIONS_MAX_ENTRIES = 80;

  function readPositions() {
    try {
      const raw = localStorage.getItem(SCROLL_POSITIONS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch {
      return {};
    }
  }

  function writePositions(positions) {
    try {
      localStorage.setItem(SCROLL_POSITIONS_STORAGE_KEY, JSON.stringify(positions));
    } catch (err) {
      console.warn('chat-scroll', err);
    }
  }

  function trimPositions(positions) {
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

  function getEntry(convId) {
    if (!convId) return null;
    const entry = readPositions()[convId];
    if (!entry || typeof entry !== 'object') return null;
    return entry;
  }

  function chatHistoryScrollEl(app) {
    return app.$.chatHistory?.querySelector('.chat-history-scroll') ?? app.$.chatHistory;
  }

  function distanceFromBottom(el) {
    if (!el) return 0;
    return el.scrollHeight - el.scrollTop - el.clientHeight;
  }

  function findScrollAnchor(app, scrollEl) {
    if (!scrollEl) return null;
    const containerTop = scrollEl.getBoundingClientRect().top;
    const rows = app.$.chatMessages?.querySelectorAll('.message-row[data-message-id]');
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

  function save(app, convId = app.currentConvId) {
    if (!convId || app._suppressScrollPositionSave) return;
    const scrollEl = chatHistoryScrollEl(app);
    if (!scrollEl) return;

    const positions = readPositions();
    const dist = distanceFromBottom(scrollEl);
    if (dist <= SCROLL_STICKY_PX) {
      positions[convId] = { atBottom: true, updatedAt: Date.now() };
    } else {
      const anchor = findScrollAnchor(app, scrollEl);
      positions[convId] = {
        atBottom: false,
        scrollTop: Math.round(scrollEl.scrollTop),
        anchorMessageId: anchor?.messageId || null,
        anchorOffset: anchor?.offset ?? 0,
        updatedAt: Date.now(),
      };
    }
    writePositions(trimPositions(positions));
  }

  function clear(convId) {
    if (!convId) return;
    const positions = readPositions();
    if (!positions[convId]) return;
    delete positions[convId];
    writePositions(positions);
  }

  function scheduleSave(app) {
    if (!app.currentConvId || app._suppressScrollPositionSave) return;
    clearTimeout(app._scrollPositionSaveTimer);
    app._scrollPositionSaveTimer = setTimeout(
      () => save(app, app.currentConvId),
      SCROLL_POSITION_SAVE_DEBOUNCE_MS,
    );
  }

  function applyScrollAnchor(app, entry) {
    const scrollEl = chatHistoryScrollEl(app);
    if (!scrollEl || !entry) return false;

    if (entry.anchorMessageId) {
      const row = app._findRow(entry.anchorMessageId);
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

  function settleAfterImages(app, entry) {
    if (!entry?.anchorMessageId) return;
    const scrollEl = chatHistoryScrollEl(app);
    if (!scrollEl) return;

    const reapply = () => {
      app._suppressScrollPositionSave = true;
      applyScrollAnchor(app, entry);
      app._onChatScroll();
      app._suppressScrollPositionSave = false;
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

  function applyRestore(app, entry) {
    const scrollEl = chatHistoryScrollEl(app);
    if (!scrollEl || !entry) return;

    const prevOverflow = scrollEl.style.overflow;
    scrollEl.style.overflow = 'hidden';
    app._suppressScrollPositionSave = true;

    if (entry.atBottom) {
      scrollEl.scrollTop = scrollEl.scrollHeight;
      app._scrollStuckToBottom = true;
    } else if (!applyScrollAnchor(app, entry)) {
      if (Number.isFinite(entry.scrollTop)) {
        scrollEl.scrollTop = entry.scrollTop;
      }
    } else {
      app._scrollStuckToBottom = false;
    }

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        scrollEl.style.overflow = prevOverflow;
        app._suppressScrollPositionSave = false;
        app._onChatScroll();
        if (!entry.atBottom) {
          settleAfterImages(app, entry);
        }
      });
    });
  }

  function restore(app, convId) {
    const entry = getEntry(convId);
    if (!entry || entry.atBottom) {
      app.scrollToBottom(true);
      return;
    }
    applyRestore(app, entry);
  }

  window.WebChatScroll = {
    chatHistoryScrollEl,
    distanceFromBottom,
    getEntry,
    save,
    clear,
    scheduleSave,
    applyRestore,
    restore,
  };
})();
