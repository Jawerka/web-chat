/**
 * WebSocket-клиент чата (reconnect, ping, dispatch событий turn).
 * Подключается до chat.js; глобальный класс ChatSocket.
 */
(function () {
  'use strict';

  const WS_MAX_RECONNECT_ATTEMPTS = 5;

  class ChatSocket {
    constructor(conversationId, handlers) {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      this.url = `${proto}//${window.location.host}/ws/${conversationId}`;
      this.conversationId = conversationId;
      this.handlers = handlers;
      this.ws = null;
      this._pingTimer = null;
      this._reconnectTimer = null;
      this._reconnectAttempt = 0;
      this._shouldReconnect = true;
      this._maxReconnectAttempts = WS_MAX_RECONNECT_ATTEMPTS;
    }

    connect() {
      if (this.ws) this.disconnect(false);
      this._shouldReconnect = true;
      this.handlers.onConnecting?.();

      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => {
        this._reconnectAttempt = 0;
        this._pingTimer = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
        this.handlers.onOpen?.();
      };
      this.ws.onmessage = (e) => {
        try {
          this._dispatch(JSON.parse(e.data));
        } catch (err) {
          window.appLog?.error('ws', 'Ошибка разбора WS-сообщения', err.message);
        }
      };
      this.ws.onclose = () => {
        clearInterval(this._pingTimer);
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
    }

    _scheduleReconnect() {
      if (!this._shouldReconnect) return;
      if (this._reconnectAttempt >= this._maxReconnectAttempts) {
        this._shouldReconnect = false;
        this.handlers.onReconnectExhausted?.(
          this._reconnectAttempt,
          this._maxReconnectAttempts,
        );
        return;
      }
      const attempt = this._reconnectAttempt + 1;
      const delay = Math.min(1000 * 2 ** this._reconnectAttempt, 15000);
      this._reconnectAttempt = attempt;
      this.handlers.onReconnecting?.(delay, attempt, this._maxReconnectAttempts);
      this._reconnectTimer = setTimeout(() => this.connect(), delay);
    }

    sendUserMessage(text, attachmentIds, integration) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        throw new Error('Нет соединения с сервером');
      }
      const payload = {
        type: 'user_message',
        text,
        attachment_ids: attachmentIds,
        ...integration,
      };
      this.ws.send(JSON.stringify(payload));
    }

    cancel() {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'cancel' }));
      }
    }

    sendRegenerate(messageId, integration = {}) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        throw new Error('Нет соединения с сервером');
      }
      this.ws.send(JSON.stringify({
        type: 'regenerate',
        message_id: messageId,
        ...integration,
      }));
    }

    _dispatch(msg) {
      const h = this.handlers;
      switch (msg.type) {
        case 'connected': h.onConnected?.(msg); break;
        case 'assistant_draft': h.onAssistantDraft?.(msg); break;
        case 'ack': h.onAck?.(msg); break;
        case 'text_delta': h.onTextDelta?.(msg.content || ''); break;
        case 'reasoning_delta': h.onReasoningDelta?.(msg.content || ''); break;
        case 'image': h.onImages?.(msg.urls || []); break;
        case 'tool_start': h.onToolStart?.(msg.name, msg.arguments); break;
        case 'tool_done': h.onToolDone?.(msg.name, msg.summary); break;
        case 'progress': h.onProgress?.(msg); break;
        case 'generation_update': h.onGenerationUpdate?.(msg); break;
        case 'done': h.onDone?.(msg); break;
        case 'error': h.onWsError?.(msg.message, msg.code, msg.error_id); break;
        default: break;
      }
    }
  }

  window.ChatSocket = ChatSocket;
  window.WebChatWs = { WS_MAX_RECONNECT_ATTEMPTS };
})();
