/**
 * Общие DOM-утилиты (P5.5): экранирование HTML и атрибутов.
 */
/* global window */

function escapeHtml(text) {
  if (text === null || text === undefined) return '';
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

/** Экранирование значения HTML-атрибута в двойных кавычках. */
function escapeAttr(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** Нормализация `detail` из FastAPI / fetch для сообщения пользователю. */
function formatApiErrorDetail(detail) {
  if (!detail) return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (typeof item === 'object' && item?.msg ? item.msg : String(item)))
      .join('; ');
  }
  return String(detail);
}

window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
window.formatApiErrorDetail = formatApiErrorDetail;
