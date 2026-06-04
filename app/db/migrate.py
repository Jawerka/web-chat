"""
Лёгкие миграции SQLite при старте (без Alembic).
"""

from __future__ import annotations

import enum
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def run_sqlite_migrations(engine: AsyncEngine) -> None:
    """Добавить новые колонки/таблицы, если их ещё нет."""
    if "sqlite" not in str(engine.url):
        return

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(attachments)"))
        cols = {row[1] for row in result.fetchall()}
        if "media_asset_id" not in cols:
            await conn.execute(
                text(
                    "ALTER TABLE attachments ADD COLUMN media_asset_id "
                    "TEXT REFERENCES media_assets(id)"
                )
            )
            logger.info("Миграция: attachments.media_asset_id")

        result = await conn.execute(text("PRAGMA table_info(prompt_macros)"))
        macro_cols = {row[1] for row in result.fetchall()}
        if "embedding_json" not in macro_cols:
            await conn.execute(
                text("ALTER TABLE prompt_macros ADD COLUMN embedding_json TEXT"),
            )
            logger.info("Миграция: prompt_macros.embedding_json")

        await _migrate_document_chunks(conn)
        await _migrate_users_and_conversation_owner(conn)
        await _migrate_users_auth(conn)
        await _migrate_preset_prompts(conn)
        await _migrate_gallery_plan_new(conn)
        await _normalize_dashed_uuid_ids(conn)
        await _normalize_sqlite_enum_names(conn)


async def _migrate_document_chunks(conn) -> None:
    """P2.3: document_chunks для RAG."""
    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_chunks'"),
    )
    if result.fetchone() is not None:
        return
    await conn.execute(
        text(
            """
            CREATE TABLE document_chunks (
                id TEXT PRIMARY KEY,
                attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL,
                embedding_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
        ),
    )
    await conn.execute(
        text("CREATE INDEX ix_document_chunks_attachment_id ON document_chunks (attachment_id)"),
    )
    await conn.execute(
        text("CREATE INDEX ix_document_chunks_conversation_id ON document_chunks (conversation_id)"),
    )
    logger.info("Миграция: таблица document_chunks")


async def _migrate_users_auth(conn) -> None:
    """P2.2: login, password_hash, role для users."""
    tables = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"),
    )
    if tables.fetchone() is None:
        return
    result = await conn.execute(text("PRAGMA table_info(users)"))
    cols = {row[1] for row in result.fetchall()}
    if "login" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN login VARCHAR(64)"))
        logger.info("Миграция: users.login")
    if "password_hash" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
        logger.info("Миграция: users.password_hash")
    if "role" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(16) DEFAULT 'user'"))
        logger.info("Миграция: users.role")
    if "is_active" not in cols:
        await conn.execute(
            text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"),
        )
        logger.info("Миграция: users.is_active")
    if "last_login_at" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME"))
        logger.info("Миграция: users.last_login_at")
    await conn.execute(text("UPDATE users SET login = slug WHERE login IS NULL OR login = ''"))
    await conn.execute(
        text(
            "UPDATE users SET password_hash = "
            "'$2b$12$W3KcBzGgzV0mgVMxeSrWFeU5hq6FumnLBKm2Yp8QRLVyInIMIQ5h.' "
            "WHERE password_hash IS NULL OR password_hash = ''",
        ),
    )
    await conn.execute(text("UPDATE users SET role = 'user' WHERE role IS NULL OR role = ''"))
    await conn.execute(text("UPDATE users SET is_active = 1 WHERE is_active IS NULL"))


async def _migrate_users_and_conversation_owner(conn) -> None:
    """P2.2: таблица users и conversations.owner_user_id."""
    users = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"),
    )
    if users.fetchone() is None:
        await conn.execute(
            text(
                """
                CREATE TABLE users (
                    id TEXT NOT NULL PRIMARY KEY,
                    slug VARCHAR(64) NOT NULL UNIQUE,
                    display_name VARCHAR(120) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP)
                )
                """
            ),
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_users_slug ON users (slug)"),
        )
        logger.info("Миграция: таблица users")

    result = await conn.execute(text("PRAGMA table_info(conversations)"))
    conv_cols = {row[1] for row in result.fetchall()}
    if "owner_user_id" not in conv_cols:
        await conn.execute(
            text(
                "ALTER TABLE conversations ADD COLUMN owner_user_id "
                "TEXT REFERENCES users(id)"
            ),
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversations_owner_user_id "
                "ON conversations (owner_user_id)"
            ),
        )
        logger.info("Миграция: conversations.owner_user_id")


async def _normalize_sqlite_enum_names(conn) -> None:
    """SQLite: имена Enum (USER) → значения StrEnum (user) для Postgres/ORM."""
    from app.db.models import MessageRole, PromptMacroCategory

    pairs: list[tuple[str, str, type[enum.StrEnum]]] = [
        ("prompt_macros", "category", PromptMacroCategory),
        ("messages", "role", MessageRole),
    ]
    for table, column, enum_cls in pairs:
        for member in enum_cls:
            await conn.execute(
                text(
                    f"UPDATE {table} SET {column} = :value "
                    f"WHERE {column} = :name",
                ),
                {"value": member.value, "name": member.name},
            )


async def _normalize_dashed_uuid_ids(conn) -> None:
    """
    Исправить UUID с дефисами в TEXT-колонках SQLite.

    SQLAlchemy Uuid ищет по 32 hex-символам; сырой INSERT str(uuid4()) оставляет дефисы —
    get_by_id и PATCH preset_id тогда возвращают 404.
    """
    presets = await conn.execute(text("SELECT COUNT(*) FROM presets WHERE id LIKE '%-%'"))
    convs = await conn.execute(
        text("SELECT COUNT(*) FROM conversations WHERE preset_id LIKE '%-%'"),
    )
    if (presets.scalar() or 0) == 0 and (convs.scalar() or 0) == 0:
        return

    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            "UPDATE conversations SET preset_id = REPLACE(preset_id, '-', '') "
            "WHERE preset_id LIKE '%-%'"
        ),
    )
    await conn.execute(
        text("UPDATE presets SET id = REPLACE(id, '-', '') WHERE id LIKE '%-%'"),
    )
    await conn.execute(text("PRAGMA foreign_keys=ON"))
    logger.info("Миграция: нормализованы UUID пресетов (убраны дефисы)")


async def _migrate_gallery_plan_new(conn) -> None:
    """Галерея загрузок: media_token, gallery_kind, sd_*, favorites.user_id."""
    import secrets

    users = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"),
    )
    if users.fetchone() is not None:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        ucols = {row[1] for row in result.fetchall()}
        if "media_token" not in ucols:
            await conn.execute(text("ALTER TABLE users ADD COLUMN media_token BLOB"))
            logger.info("Миграция: users.media_token")
        if "media_token_created_at" not in ucols:
            await conn.execute(text("ALTER TABLE users ADD COLUMN media_token_created_at DATETIME"))
            logger.info("Миграция: users.media_token_created_at")

    result = await conn.execute(text("PRAGMA table_info(media_assets)"))
    acols = {row[1] for row in result.fetchall()}
    if "owner_user_id" not in acols:
        await conn.execute(
            text("ALTER TABLE media_assets ADD COLUMN owner_user_id TEXT REFERENCES users(id)"),
        )
        logger.info("Миграция: media_assets.owner_user_id")
    if "gallery_kind" not in acols:
        await conn.execute(text("ALTER TABLE media_assets ADD COLUMN gallery_kind VARCHAR(16)"))
        logger.info("Миграция: media_assets.gallery_kind")
    if "encryption_version" not in acols:
        await conn.execute(
            text(
                "ALTER TABLE media_assets ADD COLUMN encryption_version "
                "INTEGER NOT NULL DEFAULT 0",
            ),
        )
        logger.info("Миграция: media_assets.encryption_version")
    for col in ("sd_prompt", "sd_negative", "sd_params"):
        if col not in acols:
            await conn.execute(text(f"ALTER TABLE media_assets ADD COLUMN {col} TEXT"))
            logger.info("Миграция: media_assets.%s", col)
    if "sd_meta_extracted_at" not in acols:
        await conn.execute(
            text("ALTER TABLE media_assets ADD COLUMN sd_meta_extracted_at DATETIME"),
        )
        logger.info("Миграция: media_assets.sd_meta_extracted_at")

    fav = await conn.execute(text("PRAGMA table_info(media_favorites)"))
    fcols = {row[1] for row in fav.fetchall()}
    if fcols and "user_id" not in fcols:
        await conn.execute(
            text("ALTER TABLE media_favorites ADD COLUMN user_id TEXT REFERENCES users(id)"),
        )
        logger.info("Миграция: media_favorites.user_id")

    admin = await conn.execute(
        text("SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"),
    )
    admin_row = admin.fetchone()
    if admin_row is None:
        admin = await conn.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1"))
        admin_row = admin.fetchone()
    if admin_row is not None:
        aid = admin_row[0]
        await conn.execute(
            text(
                "UPDATE media_assets SET gallery_kind = 'generation', owner_user_id = :uid "
                "WHERE mime_type LIKE 'image/%' "
                "AND (gallery_kind IS NULL OR gallery_kind = '')",
            ),
            {"uid": aid},
        )
        await conn.execute(
            text("UPDATE media_favorites SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": aid},
        )
        tok = secrets.token_bytes(32)
        await conn.execute(
            text("UPDATE users SET media_token = :tok WHERE id = :uid AND media_token IS NULL"),
            {"tok": tok, "uid": aid},
        )


async def _migrate_preset_prompts(conn) -> None:
    """Обновить промпты image_gen и добавить пресет img2img (если БД уже была заполнена)."""
    from app.db.seed import IMAGE_GEN_PROMPT, IMG2IMG_PRESET_PROMPT

    count_row = await conn.execute(text("SELECT COUNT(*) FROM presets"))
    if (count_row.scalar() or 0) == 0:
        return

    result = await conn.execute(text("SELECT id FROM presets WHERE slug = 'image_gen' LIMIT 1"))
    if result.fetchone() is not None:
        await conn.execute(
            text("UPDATE presets SET system_prompt = :prompt, name = :name WHERE slug = 'image_gen'"),
            {
                "prompt": IMAGE_GEN_PROMPT,
                "name": "Генерация с нуля (txt2img)",
            },
        )
        logger.info("Миграция: presets.image_gen")

    result = await conn.execute(text("SELECT id FROM presets WHERE slug = 'img2img' LIMIT 1"))
    if result.fetchone() is None:
        preset_id = uuid.uuid4().hex
        await conn.execute(
            text(
                "INSERT INTO presets (id, name, slug, system_prompt, is_default, sort_order) "
                "VALUES (:id, :name, 'img2img', :prompt, 0, 2)"
            ),
            {
                "id": preset_id,
                "name": "Перерисовка (img2img)",
                "prompt": IMG2IMG_PRESET_PROMPT,
            },
        )
        logger.info("Миграция: добавлен presets.img2img")
    else:
        await conn.execute(
            text(
                "UPDATE presets SET system_prompt = :prompt, name = :name, sort_order = 2 "
                "WHERE slug = 'img2img'"
            ),
            {
                "prompt": IMG2IMG_PRESET_PROMPT,
                "name": "Перерисовка (img2img)",
            },
        )
        logger.info("Миграция: presets.img2img system_prompt")

    await conn.execute(
        text("UPDATE presets SET sort_order = 3 WHERE slug = 'document_analysis'"),
    )
