"""One fresh schema per case on PostgreSQL; disposable SQLite for unit tests only."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.commerce.seed import build_knowledge_objects, build_seed_objects
from app.db.base import Base
from app.knowledge.embeddings import get_embedding_provider


@asynccontextmanager
async def isolated_environment(source: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    schema = "eval_" + uuid4().hex
    engine: AsyncEngine | None = None
    created = False
    with TemporaryDirectory(prefix="commerce-eval-") as directory:
        try:
            if source.dialect.name == "postgresql":
                async with source.begin() as connection:
                    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                    created = True
                engine = create_async_engine(
                    source.url,
                    connect_args={
                        "server_settings": {
                            "search_path": f"{schema},public",
                            "statement_timeout": "15000",
                        }
                    },
                )
            elif source.dialect.name == "sqlite":
                engine = create_async_engine(f"sqlite+aiosqlite:///{directory}/case.db")

                @event.listens_for(engine.sync_engine, "connect")
                def foreign_keys(dbapi_connection: object, _: object) -> None:
                    dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]
            else:
                raise ValueError("unsupported_evaluation_database")
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync: Base.metadata.create_all(sync, checkfirst=False)
                )
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                session.add_all(
                    [
                        *build_seed_objects()[0],
                        *build_knowledge_objects(provider=get_embedding_provider())[0],
                    ]
                )
                await session.commit()
            yield engine
        finally:
            if engine is not None:
                await engine.dispose()
            if created:
                async with source.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
