/**
 * DOM-блоки assistant-сообщений (reasoning, RAG sources).
 * Подключается до chat.js; без npm.
 */
(function () {
  'use strict';

  function buildMessageReasoning(reasoning) {
    const raw = (reasoning || '').trim();
    if (!raw) return null;
    const details = document.createElement('details');
    details.className = 'message-reasoning';
    const summary = document.createElement('summary');
    summary.className = 'message-reasoning-summary';
    summary.textContent = 'Размышления модели';
    details.appendChild(summary);
    const body = document.createElement('pre');
    body.className = 'message-reasoning-body';
    body.textContent = raw;
    details.appendChild(body);
    return details;
  }

  function buildMessageRagSources(hits) {
    if (!hits?.length) return null;
    const details = document.createElement('details');
    details.className = 'message-rag-sources';
    const summary = document.createElement('summary');
    summary.className = 'message-rag-sources-summary';
    summary.textContent = `Ответ основан на документах (${hits.length})`;
    details.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'message-rag-sources-list';
    for (const hit of hits) {
      const item = document.createElement('div');
      item.className = 'message-rag-sources-item';
      const file = document.createElement('div');
      file.className = 'message-rag-sources-file';
      const score = typeof hit.score === 'number' ? ` · ${(hit.score * 100).toFixed(0)}%` : '';
      file.textContent = `${hit.file_name || 'Документ'} #${hit.chunk_index ?? 0}${score}`;
      const snippet = document.createElement('div');
      snippet.className = 'message-rag-sources-snippet';
      snippet.textContent = hit.snippet || '';
      item.append(file, snippet);
      list.appendChild(item);
    }
    details.appendChild(list);
    return details;
  }

  window.WebChatMessageBlocks = {
    buildMessageReasoning,
    buildMessageRagSources,
  };
})();
