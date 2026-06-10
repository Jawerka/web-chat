/**
 * Базовый WebSocket: ping, exponential reconnect (P5.7).
 */
(function () {
  'use strict';

  const DEFAULT_PING_INTERVAL_MS = 30000;
  const DEFAULT_MAX_RECONNECT_ATTEMPTS = 5;

  class BaseReconnectingSocket {
    /**
     * @param {string} url
     * @param {object} [options]
     * @param {object} [options.handlers]
     * @param {number} [options.pingIntervalMs]
     * @param {number|null} [options.maxReconnectAttempts] null = без лимита
     */
    constructor(url, options = {}) {
      this.url = url;
      this.handlers = options.handlers || {};
      this.pingIntervalMs = options.pingIntervalMs ?? DEFAULT_PING_INTERVAL_MS;
      if (options.maxReconnectAttempts === null) {
        this._maxReconnectAttempts = null;
      } else if (options.maxReconnectAttempts === undefined) {
        this._maxReconnectAttempts = DEFAULT_MAX_RECONNECT_ATTEMPTS;
      } else {
        this._maxReconnectAttempts = options.maxReconnectAttempts;
      }
      this.ws = null;
      this._pingTimer = null;
      this._reconnectTimer = null;
      this._reconnectAttempt = 0;
      this._shouldReconnect = true;
      this._holdReconnect = false;
      this.connected = false;
    }

    /** Во время долгого прогрева моделей — не сдаваться после N попыток. */
    setHoldReconnect(hold) {
      this._holdReconnect = Boolean(hold);
      if (hold) {
        this._reconnectAttempt = 0;
      }
      const interval = hold ? 10000 : this.pingIntervalMs;
      if (this.ws?.readyState === WebSocket.OPEN) {
        this._startPing(interval);
      }
    }

    connect() {
      if (this.ws) this.disconnect(false);
      this._shouldReconnect = true;
      this.handlers.onConnecting?.();

      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => {
        this._reconnectAttempt = 0;
        this.connected = true;
        this._startPing();
        this.handlers.onOpen?.();
      };
      this.ws.onmessage = (e) => {
        try {
          this._handleMessage(JSON.parse(e.data));
        } catch (err) {
          this._onParseError(err);
        }
      };
      this.ws.onclose = () => {
        this._stopPing();
        this.connected = false;
        this.handlers.onClose?.();
        this._scheduleReconnect();
      };
      this.ws.onerror = () => this.handlers.onError?.();
    }

    disconnect(stopReconnect = true) {
      if (stopReconnect) this._shouldReconnect = false;
      this._stopPing();
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
      if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
        this.ws = null;
      }
      this.connected = false;
    }

    sendJson(payload) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        throw new Error('Нет соединения с сервером');
      }
      this.ws.send(JSON.stringify(payload));
    }

    _startPing(intervalMs) {
      this._stopPing();
      const ms = intervalMs ?? this.pingIntervalMs;
      this._pingTimer = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, ms);
    }

    _stopPing() {
      clearInterval(this._pingTimer);
      this._pingTimer = null;
    }

    _scheduleReconnect() {
      if (!this._shouldReconnect) return;
      const max = this._holdReconnect ? null : this._maxReconnectAttempts;
      if (max !== null && this._reconnectAttempt >= max) {
        this._shouldReconnect = false;
        this.handlers.onReconnectExhausted?.(this._reconnectAttempt, max);
        return;
      }
      const attempt = this._reconnectAttempt + 1;
      const delay = Math.min(1000 * 2 ** this._reconnectAttempt, 15000);
      this._reconnectAttempt = attempt;
      this.handlers.onReconnecting?.(delay, attempt, max);
      this._reconnectTimer = setTimeout(() => this.connect(), delay);
    }

    /** @param {object} _msg */
    _handleMessage(_msg) {
      /* override */
    }

    _onParseError(err) {
      const detail = err instanceof Error ? err : new Error(String(err));
      window.appLog?.error?.('ws', 'Ошибка разбора WS-сообщения', detail);
    }
  }

  window.BaseReconnectingSocket = BaseReconnectingSocket;
  window.WebChatReconnect = {
    DEFAULT_PING_INTERVAL_MS,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
  };
})();
