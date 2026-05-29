/**
 * WebSocket-клиент чата (reconnect, ping, dispatch событий turn).
 * Подключается до chat.js; глобальный класс ChatSocket.
 */
(function () {
  'use strict';

  const WS_MAX_RECONNECT_ATTEMPTS =
    window.WebChatReconnect?.DEFAULT_MAX_RECONNECT_ATTEMPTS ?? 5;

  class ChatSocket extends BaseReconnectingSocket {
    constructor(conversationId, handlers) {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      super(`${proto}//${window.location.host}/ws/${conversationId}`, {
        handlers,
        maxReconnectAttempts: WS_MAX_RECONNECT_ATTEMPTS,
      });
      this.conversationId = conversationId;
    }

    _handleMessage(msg) {
      const h = this.handlers;
      switch (msg.type) {
        case 'connected':
          h.onConnected?.(msg);
          break;
        case 'assistant_draft':
          h.onAssistantDraft?.(msg);
          break;
        case 'ack':
          h.onAck?.(msg);
          break;
        case 'text_delta':
          h.onTextDelta?.(msg.content || '');
          break;
        case 'reasoning_delta':
          h.onReasoningDelta?.(msg.content || '');
          break;
        case 'image':
          h.onImages?.(msg.urls || []);
          break;
        case 'tool_start':
          h.onToolStart?.(msg.name, msg.arguments);
          break;
        case 'tool_done':
          h.onToolDone?.(msg.name, msg.summary);
          break;
        case 'progress':
          h.onProgress?.(msg);
          break;
        case 'generation_update':
          h.onGenerationUpdate?.(msg);
          break;
        case 'done':
          h.onDone?.(msg);
          break;
        case 'error':
          h.onWsError?.(msg.message, msg.code, msg.error_id);
          break;
        default:
          break;
      }
    }

    sendUserMessage(llmText, attachmentIds, integration, displayText = null) {
      const payload = {
        type: 'user_message',
        text: llmText,
        attachment_ids: attachmentIds,
        ...integration,
      };
      if (displayText != null && displayText !== llmText) {
        payload.display_text = displayText;
      }
      this.sendJson(payload);
    }

    cancel() {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.sendJson({ type: 'cancel' });
      }
    }

    sendRegenerate(messageId, integration = {}, llmTextOverride = null) {
      const payload = {
        type: 'regenerate',
        message_id: messageId,
        ...integration,
      };
      const llmText = (llmTextOverride ?? '').trim();
      if (llmText) {
        payload.text = llmText;
      }
      this.sendJson(payload);
    }
  }

  window.ChatSocket = ChatSocket;
  window.WebChatWs = { WS_MAX_RECONNECT_ATTEMPTS };
})();
