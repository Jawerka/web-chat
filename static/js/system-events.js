/**
 * Подписка на /ws/events — gallery_update, logs_append (P1.3).
 */

const SYSTEM_EVENTS_POLL_FALLBACK_MS = 30000;

class SystemEventsSocket {
  constructor(handlers = {}) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.url = `${proto}//${window.location.host}/ws/events`;
    this.handlers = handlers;
    this.ws = null;
    this._pingTimer = null;
    this._reconnectTimer = null;
    this._reconnectAttempt = 0;
    this._shouldReconnect = true;
    this.connected = false;
  }

  connect() {
    if (this.ws) this.disconnect(false);
    this._shouldReconnect = true;
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this._reconnectAttempt = 0;
      this.connected = true;
      this.handlers.onOpen?.();
      this._pingTimer = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
    };
    this.ws.onmessage = (e) => {
      try {
        this._dispatch(JSON.parse(e.data));
      } catch (err) {
        window.appLog?.error?.('system-ws', 'parse error', err?.message);
      }
    };
    this.ws.onclose = () => {
      clearInterval(this._pingTimer);
      this.connected = false;
      this.handlers.onClose?.();
      this._scheduleReconnect();
    };
    this.ws.onerror = () => this.handlers.onError?.();
  }

  disconnect(stopReconnect = true) {
    if (stopReconnect) this._shouldReconnect = false;
    clearInterval(this._pingTimer);
    clearTimeout(this._reconnectTimer);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
  }

  _scheduleReconnect() {
    if (!this._shouldReconnect) return;
    const delay = Math.min(1000 * 2 ** this._reconnectAttempt, 15000);
    this._reconnectAttempt += 1;
    this._reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  _dispatch(msg) {
    const h = this.handlers;
    switch (msg.type) {
      case 'connected':
        h.onConnected?.(msg);
        break;
      case 'gallery_update':
        h.onGalleryUpdate?.(msg);
        break;
      case 'logs_append':
        h.onLogsAppend?.(msg.lines || []);
        break;
      default:
        break;
    }
  }
}
