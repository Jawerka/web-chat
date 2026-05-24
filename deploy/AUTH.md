# Аутентификация и защита данных (P2.2)

> **Контекст:** для личного LAN auth **не обязателен** (`AUTH_ENABLED=false` на dev). Включайте, если к хосту имеют доступ другие люди. Философия проекта — [HANDBOOK.md §0.5](../HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки).

## Включение

В `.env`:

```env
AUTH_ENABLED=true
AUTH_SECRET=<случайная строка ≥32 байт, openssl rand -hex 32>
AUTH_BOOTSTRAP_ADMIN_LOGIN=admin
AUTH_BOOTSTRAP_ADMIN_PASSWORD=admin   # сменить сразу после первого входа
AUTH_COOKIE_SECURE=true               # только HTTPS в production
WEB_CHAT_ENV=production               # отказ старта при слабом bootstrap-пароле
```

Перезапуск: `systemctl restart web-chat`

При старте автоматически:
1. Создаётся пользователь **admin** (если нет).
2. Все беседы с `owner_user_id IS NULL` назначаются **admin**.

Ручное назначение (опционально):

```bash
python -m app.scripts.assign_conversation_owners --user admin
```

## Вход

- Страница: `/login`
- API: `POST /api/auth/login` → HttpOnly cookie `webchat_session`
- Выход: `POST /api/auth/logout`
- Текущий пользователь: `GET /api/auth/me`
- Смена пароля: `POST /api/auth/change-password` (`current_password`, `new_password` ≥4) — cookie не сбрасывается

Браузер отправляет cookie на REST и WebSocket (same-origin).

## Изоляция данных

При `AUTH_ENABLED=true` включена изоляция бесед (`effective_multi_user`):
- список/CRUD только своих бесед;
- WS и upload с чужим `conversation_id` → отказ.

## Доверенные внутренние сервисы (LLM, SD)

При `AUTH_ENABLED=true` браузер ходит с cookie, а **llama-server / SD** — без cookie.

Автоматически в доверенные IP попадают хосты из:

- `.env`: `LLM_BASE_URL`, `SD_WEBUI_URL`, `PUBLIC_BASE_URL`, `PUBLIC_BASE_URL_VPN`
- **Настройки чата** (адреса LLM/SD в localStorage): `POST /api/config/trusted-internal/sync` при сохранении и при первом WS-сообщении

Дополнительно в `.env`:

```env
TRUSTED_INTERNAL_IPS=192.168.88.100
TRUSTED_INTERNAL_ALLOW_LOOPBACK=true
```

С доверенного IP без сессии доступны: `GET /media/asset/*`, `GET /api/health/logs`.

В настройках чата отображается подсказка «Доверенные IP (N)…».

## Безопасность (реализовано)

| Мера | Реализация |
|------|-----------|
| Пароли | bcrypt (cost 12), не хранятся в открытом виде |
| Сессии | Подпись HMAC (`itsdangerous`), срок `AUTH_SESSION_MAX_AGE_SEC` |
| Cookie | `HttpOnly`, `SameSite=Lax`, опционально `Secure` |
| Brute-force | Rate limit на `/api/auth/login` (как API) |
| CSRF | `SameSite` + same-origin fetch с credentials |
| Секрет | `AUTH_SECRET` только в `.env`, не в git |

## Управление пользователями (admin)

- **Настройки чата** → раздел «Пользователи» (виден только `role=admin`).
- `GET /api/users` — список учётных записей.
- `POST /api/users` — создать пользователя (`login`, `password`, опционально `display_name`, `role`: `user`|`admin`).
- **Смена пароля:** настройки → «Аккаунт» → форма (любой вошедший пользователь).
- **Выйти:** кнопка в разделе «Аккаунт» или `POST /api/auth/logout`.

## Дальнейшее развитие

- Деактивация пользователей, сброс пароля админом.
- Опционально: Redis-сессии, 2FA, принудительный logout всех сессий.

См. также [MULTI-USER.md](MULTI-USER.md) (legacy-заголовок).
