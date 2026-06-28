# Безопасность web-chat (домашний / LAN)

> **Контекст проекта:** личный инструмент для **одного оператора** в доверенной LAN, без публичного интернета и без хранения критичных секретов. Приоритеты и что **не** усложняем — [HANDBOOK.md §0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки).

## Минимум для домашнего стенда

1. Сервис **не** пробрасывать в открытый интернет без необходимости.
2. `PUBLIC_BASE_URL` = URL в браузере (vision и картинки в чате).
3. `.env` не в git; права на `data/` ограничены.
4. Периодический backup: `scripts/backup-all.sh` — [deploy/DATABASE-BACKUP.md](deploy/DATABASE-BACKUP.md).
5. Проверка стенда: `/health`, `GET /api/health`.

На dev можно отключить лишнее: `AUTH_ENABLED=false`, пустой `API_ACCESS_KEY`, мягкий rate limit.

## Если в сети не только вы

Тогда имеет смысл усилить контур (по желанию, не обязательно для «только я»):

| Мера | Назначение |
|------|------------|
| [deploy/AUTH.md](deploy/AUTH.md) | Login/password, изоляция бесед |
| Reverse proxy + HTTPS | Шаблон [nginx](deploy/nginx-web-chat.conf.template), [DEPLOY.md §11](deploy/DEPLOY.md) |
| `API_ACCESS_KEY` | REST/WS для внешних клиентов |
| `TRUSTED_WS_ORIGINS` | CSWSH при доступе с нескольких origin |
| `TRUSTED_PROXY_IPS` | Корректный client IP за nginx |

## Trusted internal (LLM / SD)

Браузер ходит с cookie (при auth), **llama-server и SD** — без cookie. С доверенных IP разрешены `GET /media/asset/*`, `GET /api/health/logs`, legacy `GET /api/sd-bridge/import/{token}`:

- хосты из `LLM_BASE_URL`, `SD_WEBUI_URL`, `PUBLIC_BASE_URL` (+ VPN);
- `TRUSTED_INTERNAL_IPS` в `.env`;
- адреса из настроек чата → `POST /api/config/trusted-internal/sync`.

Подробнее: [deploy/AUTH.md](deploy/AUTH.md).

**Chrome extension booru-web-chat:** permission `cookies`; session передаётся явным заголовком `Cookie`, не cross-site `credentials`. См. [extensions/booru-web-chat/API.md](extensions/booru-web-chat/API.md).

## Rate limiting

In-memory на процесс (`.env`: `RATE_LIMIT_*`). Ограничивает upload, создание бесед, WS `user_message`. Для одного пользователя можно ослабить или отключить (`RATE_LIMIT_ENABLED=false`).

## Что сознательно не в фокусе

- Prompt-injection hardening, санитизация display_name для LLM.
- DDoS-защита, Redis-сессии, горизонтальное масштабирование.

## Ссылки

- Чеклист стенда: [HANDBOOK.md §7](HANDBOOK.md#7-чеклист-перед-production)
- Реализованная платформа (auth, rate limit, SSRF): [HANDBOOK.md §21](HANDBOOK.md#21-стабилизация-и-платформа-v2-2026-05-23)
- Планируемые действия: [HANDBOOK.md §22](HANDBOOK.md#22-планируемые-действия)
