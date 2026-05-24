# Безопасность web-chat

Проект рассчитан на **доверенную LAN / VPN** (WireGuard, Tailscale). Не выставляйте порт `8090` в открытый интернет без reverse proxy, HTTPS и аутентификации.

## Рекомендуемый контур

1. **Reverse proxy** (nginx, Caddy, Traefik): HTTPS, Basic Auth или OAuth2-proxy.  
   Шаблон nginx: [`deploy/nginx-web-chat.conf.template`](deploy/nginx-web-chat.conf.template), раздел [DEPLOY.md §11](deploy/DEPLOY.md#11-reverse-proxy-nginx).
2. **API key в приложении** (опционально): переменная `API_ACCESS_KEY` в `.env`.
   - REST: заголовок `X-API-Key` или `Authorization: Bearer <key>`.
   - WebSocket: тот же заголовок или query `?api_key=<key>` до upgrade.
3. **Origin для WebSocket**: `TRUSTED_WS_ORIGINS` — список через запятую, например  
   `http://192.168.88.44:8090,http://10.99.99.9:8090`.
4. **Доверенный proxy**: `TRUSTED_PROXY_IPS` — IP nginx/Caddy, если используете `X-Forwarded-For`.
5. **Доверенные внутренние сервисы**: хосты из `LLM_BASE_URL`, `SD_WEBUI_URL`, `PUBLIC_BASE_URL` (+ VPN) автоматически резолвятся в IP; дополнительно `TRUSTED_INTERNAL_IPS`. Адреса из **настроек чата** (LLM/SD) регистрируются при сохранении и в WebSocket. С этих IP доступны без cookie: `/media/asset/*`, `/api/health/logs`. По умолчанию разрешён loopback (`TRUSTED_INTERNAL_ALLOW_LOOPBACK=true`).

Пустые `API_ACCESS_KEY` и `TRUSTED_WS_ORIGINS` отключают соответствующие проверки (режим разработки в LAN).

## Rate limiting

In-memory лимит на процесс (см. `.env`):

- `RATE_LIMIT_ENABLED=true`
- `RATE_LIMIT_REQUESTS=60` — запросов в окне
- `RATE_LIMIT_WINDOW_SEC=60`

Ограничиваются: `POST /api/upload`, `POST /api/conversations`, `DELETE /api/gallery/all`, сообщения WebSocket `user_message`.

Ответ `429` с `code: rate_limit_error`.

## PUBLIC_BASE_URL

- Должен совпадать с URL в браузере пользователя.
- Валидатор отклоняет loopback/metadata IP (кроме `localhost` для dev).
- LLM vision всегда использует LAN-base (`for_llm=True`), не Host из запроса.

## Чеклист перед выходом за LAN

- [ ] Задан `API_ACCESS_KEY` или Basic Auth на proxy
- [ ] Задан `TRUSTED_WS_ORIGINS`
- [ ] HTTPS на proxy
- [ ] `.env` не в git
- [ ] Firewall: только подсеть VPN/LAN
- [ ] Регулярный backup: `deploy/backup-data.sh`

## Вход login/password (P2.2)

При `AUTH_ENABLED=true` (рекомендуется в production):

- Сессия HttpOnly cookie, bcrypt, `AUTH_SECRET` ≥ 32 символов.
- Изоляция бесед по `owner_user_id`.
- Документация: [deploy/AUTH.md](deploy/AUTH.md).

Дополнительно к proxy/API key: cookie-сессия для браузера, API key — для внешних клиентов.

Подробнее: [TODO.md §7](TODO.md#7-чеклист-перед-production), выполненное — [§21](TODO.md#21-стабилизация-и-платформа-v2-2026-05-23), план — [§22](TODO.md#22-планируемые-действия).
