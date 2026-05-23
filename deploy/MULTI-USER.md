# Multi-user (P2.2)

> **Рекомендуется:** вход по логину/паролю — **[AUTH.md](AUTH.md)** (`AUTH_ENABLED=true`).

Legacy-режим: изоляция по заголовку **`X-Web-Chat-User`** (`AUTH_ENABLED=false`, `MULTI_USER_ENABLED=true`).

## Включение

1. Применить миграции БД: `python -m app.scripts.db_upgrade`
2. Назначить владельца legacy-беседам (до включения флага):

```bash
python -m app.scripts.assign_conversation_owners --user default
python -m app.scripts.assign_conversation_owners --user default --dry-run
```

3. В `.env`:

```env
MULTI_USER_ENABLED=true
# опционально:
MULTI_USER_MAX_CONVERSATIONS=100
MULTI_USER_MAX_UPLOADS_PER_DAY=500
```

4. Перезапуск: `systemctl restart web-chat`

## Клиент

Браузер / reverse proxy должен передавать один и тот же slug на REST и WebSocket:

```http
X-Web-Chat-User: alice
```

Без заголовка используется slug **`default`**.

## Поведение

| `MULTI_USER_ENABLED` | Список бесед | `owner_user_id` при создании |
|----------------------|--------------|------------------------------|
| `false` | все беседы | `NULL` (как раньше) |
| `true` | только свои | id пользователя из заголовка |

Чужая `conversation_id` в REST/WS → **404** / закрытие WS.

## Квоты

- `MULTI_USER_MAX_CONVERSATIONS` — макс. бесед на пользователя (`0` = без лимита)
- `MULTI_USER_MAX_UPLOADS_PER_DAY` — вложения в беседах пользователя за 24 ч (`0` = без лимита)

Ответ при превышении: HTTP **403**, `{"code":"quota_exceeded","message":"..."}`.
