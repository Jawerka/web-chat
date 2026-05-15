/**
 * Markdown → HTML (порт из prompt-extension/sidebar.js).
 */
/* global window */

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

function sanitizeHtml(html) {
  if (!html || typeof html !== 'string') return html;
  let sanitized = html;
  sanitized = sanitized.replace(/<script[\s\S]*?<\/script>/gi, '');
  sanitized = sanitized.replace(/<script[\s\S]*/gi, '');
  sanitized = sanitized.replace(/javascript\s*:/gi, 'blocked:');
  sanitized = sanitized.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '');
  sanitized = sanitized.replace(/<(?:iframe|object|embed)[\s\S]*?(?:<\/\w+>|\/?>)/gi, '');
  sanitized = sanitized.replace(/<style[\s\S]*?<\/style>/gi, '');
  return sanitized;
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
  formatted = formatted.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  formatted = formatted.replace(
    /!\[(.+?)\]\((.+?)\)/g,
    '<img src="$2" alt="$1" class="md-inline-img" loading="lazy">',
  );
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

window.escapeHtml = escapeHtml;
window.sanitizeHtml = sanitizeHtml;
window.formatMarkdown = formatMarkdown;
window.parseTables = parseTables;
