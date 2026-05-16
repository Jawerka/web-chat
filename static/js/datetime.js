/**
 * Даты API (UTC) → отображение в часовом поясе пользователя или Europe/Moscow.
 */
(function initWebChatDateTime(global) {
  const STORAGE_KEY = 'webchat_display_timezone';

  function getDisplayTimeZone() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && saved !== 'auto') {
      return saved;
    }
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Europe/Moscow';
    } catch {
      return 'Europe/Moscow';
    }
  }

  function setDisplayTimeZone(tz) {
    if (!tz || tz === 'auto') {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, tz);
    }
  }

  function applyServerDefault(serverTz) {
    if (localStorage.getItem(STORAGE_KEY)) return;
    if (serverTz && serverTz !== 'auto') {
      localStorage.setItem(STORAGE_KEY, serverTz);
    }
  }

  /** Naive ISO из API трактуем как UTC. */
  function parseApiDateTime(value) {
    if (value == null || value === '') return null;
    const s = String(value).trim();
    if (!s) return null;
    if (/[zZ]$/.test(s) || /[+-]\d{2}:?\d{2}$/.test(s)) {
      const d = new Date(s);
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const d = new Date(`${s}Z`);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatDateTime(value, options = {}) {
    const d = value instanceof Date ? value : parseApiDateTime(value);
    if (!d) return '—';
    const fmt = {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: getDisplayTimeZone(),
      ...options,
    };
    return d.toLocaleString('ru-RU', fmt);
  }

  global.WebChatDateTime = {
    STORAGE_KEY,
    getDisplayTimeZone,
    setDisplayTimeZone,
    applyServerDefault,
    parseApiDateTime,
    formatDateTime,
  };
})(typeof window !== 'undefined' ? window : globalThis);
