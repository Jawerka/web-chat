# RAG по документам беседы (P2.3)

Семантический поиск по `extracted_text` вложений (PDF, DOCX, TXT). Отдельно от embeddings макросов (@alias, Ф2).

## Включение

В `.env` (нужна та же embedding-модель, что для макросов):

```env
RAG_ENABLED=true
RAG_AUTO_INJECT=true          # top-K фрагментов в system prompt при каждом сообщении
EMBEDDING_MODEL=text-embedding-nomic-embed-text-v1.5
RAG_CHUNK_CHARS=1500
RAG_CHUNK_OVERLAP=200
RAG_SEARCH_TOP_K=5
RAG_CONTEXT_MAX_CHARS=8000
```

Перезапуск: `systemctl restart web-chat`

## Как работает

1. После `extract_text` (upload или tool) — фоновая индексация чанков + embeddings.
2. `GET /api/conversations/{id}/document-search?q=` — поиск по беседе.
3. `POST /api/attachments/{id}/index-rag` — переиндексация вручную.
4. При `RAG_AUTO_INJECT=true` — релевантные фрагменты добавляются в system prompt (как `macro_context=semantic` для макросов).

## UI в чате

При `RAG_ENABLED=true` в composer появляется кнопка с иконкой документа:

- **Выкл** — RAG не подмешивается (если не включён `RAG_AUTO_INJECT` на сервере).
- **Вкл** — при отправке в system prompt добавляются top-K фрагментов; над полем ввода показывается превью совпадений (debounce 400 ms, от 3 символов).

Состояние кнопки хранится в `sessionStorage` (`webchat_document_rag_enabled`).

## Огранения пилота

- Только документы текущей беседы (не глобальный корпус).
- Keyword fallback, если embeddings недоступны.

См. [TODO-2.md](../TODO-2.md) § P2.3.
