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

  /** Группа для sidebar: Сегодня / Вчера / На этой неделе / Ранее */
  function formatDateGroup(value) {
    const d = value instanceof Date ? value : parseApiDateTime(value);
    if (!d) return 'Ранее';
    const tz = getDisplayTimeZone();
    const nowParts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(new Date());
    const part = (type) => nowParts.find((p) => p.type === type)?.value;
    const todayKey = `${part('year')}-${part('month')}-${part('day')}`;
    const convParts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(d);
    const convKey = `${convParts.find((p) => p.type === 'year')?.value}-${convParts.find((p) => p.type === 'month')?.value}-${convParts.find((p) => p.type === 'day')?.value}`;
    if (convKey === todayKey) return 'Сегодня';
    const todayMs = Date.parse(`${todayKey}T12:00:00`);
    const convMs = Date.parse(`${convKey}T12:00:00`);
    const dayDiff = Math.round((todayMs - convMs) / 86400000);
    if (dayDiff === 1) return 'Вчера';
    if (dayDiff >= 2 && dayDiff < 7) return 'На этой неделе';
    return 'Ранее';
  }

  global.WebChatDateTime = {
    STORAGE_KEY,
    getDisplayTimeZone,
    setDisplayTimeZone,
    applyServerDefault,
    parseApiDateTime,
    formatDateTime,
    formatDateGroup,
  };
})(typeof window !== 'undefined' ? window : globalThis);
