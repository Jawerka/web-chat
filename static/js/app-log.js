/**
 * Журнал сессии web-chat (клиент + опционально сервер).
 */
class AppLog {
  constructor(maxEntries = 500) {
    this.entries = [];
    this.maxEntries = maxEntries;
    this.listeners = new Set();
  }

  _timestamp() {
    const d = new Date();
    const pad = (n, w = 2) => String(n).padStart(w, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
  }

  _formatDetail(detail) {
    if (detail == null) return '';
    if (typeof detail === 'string') return ` — ${detail}`;
    try {
      return ` — ${JSON.stringify(detail)}`;
    } catch {
      return ` — ${String(detail)}`;
    }
  }

  log(level, category, message, detail = null) {
    const line = `[${this._timestamp()}] [${level.toUpperCase()}] [${category}] ${message}${this._formatDetail(detail)}`;
    this.entries.push({ level, line, category, message });
    if (this.entries.length > this.maxEntries) {
      this.entries.splice(0, this.entries.length - this.maxEntries);
    }
    this._notify();
    if (level === 'error') console.error(line);
    else if (level === 'warn') console.warn(line);
    else console.info(line);
  }

  info(category, message, detail) {
    this.log('info', category, message, detail);
  }

  warn(category, message, detail) {
    this.log('warn', category, message, detail);
  }

  error(category, message, detail) {
    this.log('error', category, message, detail);
  }

  getText() {
    return this.entries.map((e) => e.line).join('\n');
  }

  clear() {
    this.entries = [];
    this._notify();
  }

  subscribe(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  _notify() {
    for (const fn of this.listeners) {
      try {
        fn(this.entries);
      } catch {
        /* ignore listener errors */
      }
    }
  }
}

window.appLog = new AppLog();
