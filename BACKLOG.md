# BACKLOG — открытые задачи

> **Архитектура и принципы:** [HANDBOOK.md](HANDBOOK.md) — [§0.5](HANDBOOK.md#05-модель-эксплуатации-и-приоритеты-разработки).  
> **Статус:** `pytest -q` → **289 passed** (2026-05-24).  
> **Инциденты:** [docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Снимок (техдолг)

| Область | Состояние |
|---------|-----------|
| `static/js/chat.js` | ~5.8k строк — разбиение в P3 |
| `MediaAsset.data` | BLOB — ок для одного пользователя |

---

## P2 — Backend

| ID | Задача |
|----|--------|
| BE-SD-HTTPX | `httpx` для SD — после замера |
| BE-MEDIA-FS | BLOB → filesystem — при боли бэкапов |

---

## P2 — Эксплуатация (если сеть не только ваша)

- [ ] nginx + HTTPS · смена пароля в UI · квартальный restore — см. [deploy/](deploy/)

---

## P3 — DX

- [ ] Разбить `chat.js` на модули без npm
- [ ] `docker-compose.dev.yml` — при втором разработчике

---

## Gate (ручная проверка на стенде)

txt2img → PDF + RAG (источники после F5) → корзина → F5 mid-stream (без дубля картинок) → reasoning (если модель отдаёт).

---

## Регрессия

```bash
source .venv/bin/activate
ruff check app tests && pytest -q
```

---

## Синхронизация с HANDBOOK

Закрыли задачу → [журнал](HANDBOOK.md#журнал-прогресса) → удалить пункт **отсюда**.

---

*Обновлено: 2026-05-24.*
