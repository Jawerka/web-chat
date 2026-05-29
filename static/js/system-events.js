/**
 * Подписка на /ws/events — gallery_update, logs_append (P1.3).
 * Reconnect без лимита (фоновые страницы gallery/health).
 */

const SYSTEM_EVENTS_POLL_FALLBACK_MS = 30000;

class SystemEventsSocket extends BaseReconnectingSocket {
  constructor(handlers = {}) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    super(`${proto}//${window.location.host}/ws/events`, {
      handlers,
      maxReconnectAttempts: null,
    });
  }

  _handleMessage(msg) {
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

  _onParseError(err) {
    window.appLog?.error?.('system-ws', 'parse error', err?.message || String(err));
  }
}
