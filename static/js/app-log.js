/**
 * Журнал сессии web-chat (клиент + опционально сервер).
 */
class AppLog {
  constructor(maxEntries = 500) {
    this.entries = [];
    this.maxEntries = maxEntries;
    this.listeners = new Set();
    this._shipQueue = [];
    this._shipTimer = null;
  }

  _timestamp() {
    const d = new Date();
    const pad = (n, w = 2) => String(n).padStart(w, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
  }

  _formatDetail(detail) {
    if (detail == null) return '';
    if (detail instanceof Error) {
      const stack = detail.stack ? `\n${detail.stack}` : '';
      return ` — ${detail.message}${stack}`;
    }
    if (typeof detail === 'string') return ` — ${detail}`;
    try {
      return ` — ${JSON.stringify(detail)}`;
    } catch {
      return ` — ${String(detail)}`;
    }
  }

  _scheduleShip(line) {
    this._shipQueue.push(line);
    if (this._shipTimer) return;
    this._shipTimer = setTimeout(() => {
      const batch = this._shipQueue.splice(0, 80);
      this._shipTimer = null;
      if (!batch.length) return;
      fetch('/api/logs/client', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ lines: batch }),
      }).catch(() => {});
    }, 2000);
  }

  _shouldShip(level, category, ship) {
    if (ship === false) return false;
    if (ship === true) return true;
    if (level === 'debug') return false;
    if (category === 'health' && level !== 'error' && level !== 'warn') return false;
    return true;
  }

  log(level, category, message, detail = null, options = {}) {
    const line = `[${this._timestamp()}] [${level.toUpperCase()}] [${category}] ${message}${this._formatDetail(detail)}`;
    this.entries.push({ level, line, category, message });
    if (this.entries.length > this.maxEntries) {
      this.entries.splice(0, this.entries.length - this.maxEntries);
    }
    this._notify();
    if (this._shouldShip(level, category, options.ship)) {
      this._scheduleShip(line);
    }
    if (level === 'error') console.error(line);
    else if (level === 'warn') console.warn(line);
    else if (level === 'debug') console.debug(line);
    else console.info(line);
  }

  debug(category, message, detail, options) {
    this.log('debug', category, message, detail, options);
  }

  info(category, message, detail, options) {
    this.log('info', category, message, detail, options);
  }

  warn(category, message, detail, options) {
    this.log('warn', category, message, detail, options);
  }

  error(category, message, detail, options) {
    this.log('error', category, message, detail, options);
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
