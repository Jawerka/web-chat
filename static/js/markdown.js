/**
 * Markdown → HTML (порт из prompt-extension/sidebar.js).
 * P5.2: финальная санитизация через DOMPurify + allowlist URI.
 */
/* global window, DOMPurify, escapeHtml */

/** @param {string} uri @param {{ forImage?: boolean }} [opts] */
function isAllowedUri(uri, opts = {}) {
  if (!uri || typeof uri !== 'string') return false;
  const t = uri.trim();
  if (!t || /^\s*(javascript|vbscript|data):/i.test(t)) return false;
  if (t.startsWith('//')) return false;
  if (t.startsWith('/media/') || t.startsWith('/static/')) return true;
  if (t.startsWith('/') && !t.startsWith('//')) {
    return opts.forImage ? t.startsWith('/media/') : true;
  }
  try {
    const u = new URL(t, window.location.origin);
    if (u.origin === window.location.origin) {
      return opts.forImage ? u.pathname.startsWith('/media/') : true;
    }
  } catch {
    return false;
  }
  if (!opts.forImage && /^https?:\/\//i.test(t)) return true;
  return false;
}

/** Regex fallback when DOMPurify недоступен (тесты, старый кэш). */
function sanitizeHtmlLegacy(html) {
  if (!html || typeof html !== 'string') return html;
  let sanitized = html;
  sanitized = sanitized.replace(/<script[\s\S]*?<\/script>/gi, '');
  sanitized = sanitized.replace(/<script[\s\S]*/gi, '');
  sanitized = sanitized.replace(/javascript\s*:/gi, 'blocked:');
  sanitized = sanitized.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '');
  sanitized = sanitized.replace(/<(?:iframe|object|embed)[\s\S]*?(?:<\/\w+>|\s*\/?\s*>)/gi, '');
  sanitized = sanitized.replace(/<style[\s\S]*?<\/style>/gi, '');
  return sanitized;
}

let _dompurifyUriHookInstalled = false;

function ensureDompurifyUriHook() {
  if (_dompurifyUriHookInstalled || typeof DOMPurify === 'undefined') return;
  DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
    if (data.attrName !== 'href' && data.attrName !== 'src') return;
    const forImage = data.attrName === 'src' || node.tagName === 'IMG';
    if (!isAllowedUri(data.attrValue, { forImage })) {
      data.attrValue = '';
      data.keepAttr = false;
    }
  });
  _dompurifyUriHookInstalled = true;
}

function sanitizeHtml(html) {
  if (!html || typeof html !== 'string') return html;
  if (typeof DOMPurify !== 'undefined' && DOMPurify.sanitize) {
    ensureDompurifyUriHook();
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'a',
        'blockquote',
        'br',
        'code',
        'del',
        'em',
        'h1',
        'h2',
        'h3',
        'h4',
        'h5',
        'h6',
        'hr',
        'img',
        'li',
        'ol',
        'p',
        'pre',
        'strong',
        'table',
        'tbody',
        'td',
        'th',
        'thead',
        'tr',
        'ul',
      ],
      ALLOWED_ATTR: ['href', 'src', 'alt', 'class', 'target', 'rel', 'loading'],
      ALLOW_DATA_ATTR: false,
    });
  }
  return sanitizeHtmlLegacy(html);
}

function buildTable(rows) {
  if (rows.length < 3) return rows.join('\n');
  const headers = rows[0].split('|').filter((c) => c.trim());
  const headerCells = headers.map((h) => `<th>${h.trim()}</th>`).join('');
  const dataRows = rows.slice(2).map((row) => {
    const cells = row.split('|').filter((c) => c.trim());
    const cellsHtml = cells.map((c) => `<td>${c.trim()}</td>`).join('');
    return `<tr>${cellsHtml}</tr>`;
  }).join('');
  return `<table><thead><tr>${headerCells}</tr></thead><tbody>${dataRows}</tbody></table>`;
}

function parseTables(text) {
  const lines = text.split('\n');
  const result = [];
  let inTable = false;
  let tableRows = [];

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    const isTableRow = /^\|.*\|$/.test(line);
    const isSeparator = /^\|?[-:| ]+\|?$/.test(line) && line.includes('-');

    if (isTableRow && !inTable) {
      inTable = true;
      tableRows = [line];
    } else if (isTableRow && inTable) {
      tableRows.push(line);
    } else if (isSeparator && inTable) {
      tableRows.push(line);
    } else {
      if (inTable && tableRows.length >= 3) {
        result.push(buildTable(tableRows));
        inTable = false;
        tableRows = [];
      } else if (inTable) {
        result.push(...tableRows);
        inTable = false;
        tableRows = [];
      }
      result.push(line);
    }
  }

  if (inTable && tableRows.length >= 3) {
    result.push(buildTable(tableRows));
  } else if (inTable) {
    result.push(...tableRows);
  }

  return result.join('\n');
}

function formatMarkdown(text) {
  let formatted = escapeHtml(text || '');

  formatted = formatted.replace(/```(\w*)\n([\s\S]+?)```/g, '<pre><code class="language-$1">$2</code></pre>');
  formatted = parseTables(formatted);

  formatted = formatted.replace(/^###### (.+)$/gm, '<h6>$1</h6>');
  formatted = formatted.replace(/^##### (.+)$/gm, '<h5>$1</h5>');
  formatted = formatted.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  formatted = formatted.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  formatted = formatted.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  formatted = formatted.replace(/^# (.+)$/gm, '<h1>$1</h1>');

  formatted = formatted.replace(/\*\*\*(.+?)\*\*\*/g, '<em><strong>$1</strong></em>');
  formatted = formatted.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  formatted = formatted.replace(/\*(.+?)\*/g, '<em>$1</em>');
  formatted = formatted.replace(/~~(.+?)~~/g, '<del>$1</del>');
  formatted = formatted.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  formatted = formatted.replace(/^---$/gm, '<hr>');
  formatted = formatted.replace(/^\s*[-*+]\s+(.+)$/gm, '<li>$1</li>');
  formatted = formatted.replace(/^\s*\d+\.\s+(.+)$/gm, '<li>$1</li>');
  formatted = formatted.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
  formatted = formatted.replace(
    /!\[(.+?)\]\((.+?)\)/g,
    '<img src="$2" alt="$1" class="md-inline-img" loading="lazy">',
  );
  formatted = formatted.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');
  formatted = formatted.replace(/\n\n+/g, '</p><p>');
  formatted = formatted.replace(/\n/g, '<br>');

  if (!formatted.startsWith('<')) {
    formatted = `<p>${formatted}</p>`;
  }

  const blockTags = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'pre', 'blockquote', 'table', 'hr', 'li', 'p'];
  for (const tag of blockTags) {
    formatted = formatted.replace(new RegExp(`</${tag}><br>`, 'gi'), `</${tag}>`);
    formatted = formatted.replace(new RegExp(`<br><${tag}`, 'gi'), `<${tag}`);
  }

  formatted = formatted.replace(/<br>$/g, '');
  formatted = formatted.replace(/^<br>/, '');
  formatted = formatted.replace(/<p><\/p>/g, '');

  return sanitizeHtml(formatted);
}

const CODE_COPY_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

const CODE_CHECK_ICON =
  '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';

function extractPreCode(pre) {
  const codeEl = pre.querySelector('code');
  if (codeEl) return (codeEl.textContent || '').trim();
  const clone = pre.cloneNode(true);
  clone.querySelectorAll('.code-copy-btn').forEach((el) => el.remove());
  return (clone.textContent || '').trim();
}

async function copyTextToClipboard(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
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

function flashCodeCopySuccess(btn) {
  if (!btn) return;
  clearTimeout(btn._copyFlashTimer);
  const prev = {
    html: btn.innerHTML,
    title: btn.title,
    label: btn.getAttribute('aria-label'),
  };
  btn.classList.add('is-copied');
  btn.innerHTML = CODE_CHECK_ICON;
  btn.title = 'Скопировано';
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

function enhanceCodeCopyButtons(root) {
  if (!root) return;
  root.querySelectorAll('pre').forEach((pre) => {
    if (pre.querySelector('.code-copy-btn')) return;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'code-copy-btn';
    btn.title = 'Копировать код';
    btn.setAttribute('aria-label', 'Копировать код');
    btn.innerHTML = CODE_COPY_ICON;
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      e.preventDefault();
      const code = extractPreCode(pre);
      if (!code) {
        if (window.WebChatToast) window.WebChatToast.show('Нет кода для копирования', 'error');
        return;
      }
      const ok = await copyTextToClipboard(code);
      if (ok) {
        flashCodeCopySuccess(btn);
      } else if (window.WebChatToast) {
        window.WebChatToast.show('Не удалось скопировать код', 'error');
      }
    });
    pre.classList.add('has-copy-btn');
    pre.appendChild(btn);
  });
}

window.isAllowedUri = isAllowedUri;
window.sanitizeHtml = sanitizeHtml;
window.sanitizeHtmlLegacy = sanitizeHtmlLegacy;
window.formatMarkdown = formatMarkdown;
window.enhanceCodeCopyButtons = enhanceCodeCopyButtons;
window.parseTables = parseTables;
