from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.job import Base, SchemaVersion


CURRENT_SCHEMA_VERSION = 1


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}", future=True, pool_pre_ping=True
        )

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        self._session_factory = sessionmaker(
            bind=self.engine, class_=Session, expire_on_commit=False
        )

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            current = session.scalar(
                select(SchemaVersion).order_by(SchemaVersion.version.desc()).limit(1)
            )
            if current is None:
                session.add(SchemaVersion(version=CURRENT_SCHEMA_VERSION))
            elif current.version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {current.version} is newer than supported "
                    f"schema {CURRENT_SCHEMA_VERSION}"
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
