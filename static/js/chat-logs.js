/**
 * Панель журнала (клиент + сервер) (P5.4).
 * Подключается до chat.js; API: WebChatLogs.*
 */
(function () {
  'use strict';

  async function fetchServerLogs(app) {
    try {
      const res = await fetch('/api/logs?limit=300', { credentials: 'same-origin' });
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = await res.json();
          detail = body.detail || detail;
          if (typeof detail !== 'string') detail = JSON.stringify(detail);
        } catch {
          /* ignore */
        }
        app._serverLogLines = [];
        app.log?.error('app', `Серверный журнал: HTTP ${res.status}`, detail);
        return;
      }
      const data = await res.json();
      app._serverLogLines = data.lines || [];
      app.log?.debug('app', 'Серверный журнал загружен', { lines: app._serverLogLines.length });
    } catch (err) {
      app._serverLogLines = [];
      app.log?.error('app', 'Не удалось загрузить серверный журнал', err);
    }
  }

  function renderView(app) {
    const parts = [];
    if (app._serverLogLines.length) {
      parts.push('=== Сервер ===');
      parts.push(...app._serverLogLines);
      parts.push('');
    }
    parts.push('=== Клиент (сессия) ===');
    parts.push(app.log?.getText() || '');
    const text = parts.join('\n');
    app.$.logsOutput.value = text;
    const lineCount = text.split('\n').filter((l) => l.length > 0).length;
    app.$.logsCount.textContent = `${lineCount} строк`;
  }

  function stopLiveUpdate(app) {
    if (app._logsUnsub) {
      app._logsUnsub();
      app._logsUnsub = null;
    }
  }

  async function openPanel(app) {
    await fetchServerLogs(app);
    renderView(app);
    stopLiveUpdate(app);
    app._logsUnsub = app.log?.subscribe(() => {
      if (app._isLogsPanelOpen()) renderView(app);
    }) || null;
    app.showPanel('logs');
    requestAnimationFrame(() => {
      if (app.$.logsOutput) {
        app.$.logsOutput.scrollTop = app.$.logsOutput.scrollHeight;
      }
    });
  }

  function closePanel(app) {
    app.showPanel('main');
  }

  async function copyAll(app) {
    const text = app.$.logsOutput.value;
    try {
      await navigator.clipboard.writeText(text);
      app.log?.info('app', 'Журнал скопирован в буфер обмена');
      if (app._isLogsPanelOpen()) renderView(app);
    } catch {
      app.$.logsOutput.focus();
      app.$.logsOutput.select();
      document.execCommand('copy');
    }
  }

  async function clearAll(app) {
    if (!confirm('Очистить журнал клиента и сервера?')) return;
    app.log?.clear();
    app._serverLogLines = [];
    try {
      await fetch('/api/logs', { method: 'DELETE' });
    } catch {
      /* ignore */
    }
    renderView(app);
    app.log?.info('app', 'Журнал очищен');
  }

  window.WebChatLogs = {
    fetchServerLogs,
    renderView,
    stopLiveUpdate,
    openPanel,
    closePanel,
    copyAll,
    clearAll,
  };
})();
