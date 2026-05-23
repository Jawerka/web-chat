#!/usr/bin/env python3
"""Сверка SQLite (источник) и PostgreSQL (приёмник) после ETL."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.etl_sqlite_to_postgres import ETL_TABLES, create_etl_engine
from app.db.models import Attachment, Conversation, MediaAsset, Message, Preset, PromptMacro
from app.db.url import is_postgres_url, is_sqlite_url


@dataclass
class TableReport:
    name: str
    sqlite_total: int = 0
    sqlite_migratable: int = 0
    postgres: int = 0
    ids_only_sqlite: set[uuid.UUID] = field(default_factory=set)
    ids_only_postgres: set[uuid.UUID] = field(default_factory=set)
    size_mismatch: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            not self.ids_only_sqlite
            and not self.ids_only_postgres
            and not self.size_mismatch
            and self.postgres == self.sqlite_migratable
        )


async def _count(session, model) -> int:
    r = await session.execute(select(func.count()).select_from(model))
    return int(r.scalar() or 0)


async def _all_ids(session, model) -> set[uuid.UUID]:
    pk = model.__table__.primary_key.columns.values()
    col = list(pk)[0]
    r = await session.execute(select(col))
    return set(r.scalars().all())


async def _sqlite_migratable_counts(sqlite_url: str) -> dict[str, int]:
    """Сколько строк реально должно попасть в Postgres (как ETL)."""
    engine = create_etl_engine(sqlite_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, int] = {}
    async with Session() as s:
        conv_ids = set(
            (await s.execute(select(Conversation.id))).scalars().all(),
        )
        msg_ids = set(
            (
                await s.execute(
                    select(Message.id).where(Message.conversation_id.in_(conv_ids)),
                )
            ).scalars().all(),
        )
        media_ids = set((await s.execute(select(MediaAsset.id))).scalars().all())

        out["presets"] = await _count(s, Preset)
        out["prompt_macros"] = await _count(s, PromptMacro)
        out["conversations"] = await _count(s, Conversation)
        out["media_assets"] = await _count(s, MediaAsset)
        out["messages"] = len(msg_ids)
        out["attachments"] = await _count(s, Attachment)
    await engine.dispose()
    return out


async def _compare_media_samples(
    src_sess,
    tgt_sess,
    *,
    sample: int,
) -> list[str]:
    """Сверка length(data) и md5 для случайной выборки id."""
    issues: list[str] = []
    r = await src_sess.execute(select(MediaAsset.id).limit(sample))
    ids = list(r.scalars().all())
    for mid in ids:
        src_row = await src_sess.get(MediaAsset, mid)
        tgt_row = await tgt_sess.get(MediaAsset, mid)
        if tgt_row is None:
            issues.append(f"media {mid}: missing in postgres")
            continue
        if len(src_row.data) != len(tgt_row.data):
            issues.append(
                f"media {mid}: data len {len(src_row.data)} vs {len(tgt_row.data)}",
            )
        elif hashlib.md5(src_row.data).digest() != hashlib.md5(tgt_row.data).digest():
            issues.append(f"media {mid}: data md5 mismatch")
    return issues


async def run_verify(sqlite_url: str, postgres_url: str, *, media_sample: int) -> int:
    if not is_sqlite_url(sqlite_url) or not is_postgres_url(postgres_url):
        print("Нужны sqlite source и postgres target", file=sys.stderr)
        return 2

    src_engine = create_etl_engine(sqlite_url)
    tgt_engine = create_etl_engine(postgres_url)
    Src = async_sessionmaker(src_engine, expire_on_commit=False)
    Tgt = async_sessionmaker(tgt_engine, expire_on_commit=False)

    migratable = await _sqlite_migratable_counts(sqlite_url)
    reports: list[TableReport] = []
    all_ok = True

    try:
        async with Src() as src, Tgt() as tgt:
            for name, model in ETL_TABLES:
                rep = TableReport(name=name)
                rep.sqlite_total = await _count(src, model)
                rep.sqlite_migratable = migratable.get(name, rep.sqlite_total)
                rep.postgres = await _count(tgt, model)
                src_ids = await _all_ids(src, model)
                tgt_ids = await _all_ids(tgt, model)
                if name == "messages":
                    conv_ids = await _all_ids(src, Conversation)
                    r = await src.execute(
                        select(Message.id).where(
                            Message.conversation_id.in_(conv_ids),
                        ),
                    )
                    src_ids = set(r.scalars().all())
                rep.ids_only_sqlite = src_ids - tgt_ids
                rep.ids_only_postgres = tgt_ids - src_ids
                if not rep.ok:
                    all_ok = False
                reports.append(rep)

            media_issues = await _compare_media_samples(
                src, tgt, sample=media_sample,
            )
            if media_issues:
                all_ok = False
    finally:
        await src_engine.dispose()
        await tgt_engine.dispose()

    print("=== Сверка миграции SQLite → PostgreSQL ===\n")
    print(f"{'Таблица':<16} {'SQLite':>8} {'→PG':>8} {'Postgres':>8}  Статус")
    print("-" * 52)
    for r in reports:
        st = "OK" if r.ok else "FAIL"
        print(
            f"{r.name:<16} {r.sqlite_total:>8} {r.sqlite_migratable:>8} "
            f"{r.postgres:>8}  {st}",
        )
        if r.ids_only_sqlite:
            print(f"  только в SQLite: {len(r.ids_only_sqlite)} id")
        if r.ids_only_postgres:
            print(f"  только в Postgres: {len(r.ids_only_postgres)} id")

    if media_issues:
        print(f"\nПроблемы media (выборка {media_sample}):")
        for line in media_issues[:20]:
            print(f"  {line}")
        if len(media_issues) > 20:
            print(f"  ... ещё {len(media_issues) - 20}")

    async with Tgt() as tgt:
        fk = await tgt.execute(
            text(
                """
                SELECT 'media_bad_conv' t, count(*)::int FROM media_assets m
                WHERE m.conversation_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = m.conversation_id)
                UNION ALL
                SELECT 'msg_bad_conv', count(*)::int FROM messages m
                WHERE NOT EXISTS (SELECT 1 FROM conversations c WHERE c.id = m.conversation_id)
                """,
            ),
        )
        fk_rows = fk.fetchall()
        bad_fk = sum(row[1] for row in fk_rows)
        print(f"\nНарушения FK в Postgres: {bad_fk}")
        if bad_fk:
            all_ok = False

    print("\n" + ("ИТОГ: миграция согласована." if all_ok else "ИТОГ: есть расхождения."))
    return 0 if all_ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Verify SQLite→Postgres migration")
    p.add_argument("--source", default="sqlite+aiosqlite:///./data/db/web_chat.sqlite")
    p.add_argument("--target", required=True)
    p.add_argument("--media-sample", type=int, default=15)
    args = p.parse_args()
    return asyncio.run(run_verify(args.source, args.target, media_sample=args.media_sample))


if __name__ == "__main__":
    sys.exit(main())
