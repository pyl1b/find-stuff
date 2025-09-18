"""SQLAlchemy ORM models and helpers for the inverted index database.
This module defines the database schema using SQLAlchemy ORM, along with
helpers to create a SQLite engine and initialize the schema with the
appropriate pragmas. The schema mirrors the one used by the original
``sqlite3``-based implementation to maintain full compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (  # type: ignore[import-not-found]
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""

    pass


class Repository(Base):
    __tablename__ = "repositories"

    """Git repository tracked in the index.

    Attributes:
        id: Surrogate primary key.
        root: Absolute path to the repository root (unique).
        files: Collection of files within this repository.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    root: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    files: Mapped[List["File"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan"
    )


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("repo_id", "relpath", name="uix_files_repo_relpath"),
    )

    """File tracked in a repository.

    Attributes:
        id: Surrogate primary key.
        repo_id: Foreign key to ``repositories.id``.
        relpath: File path relative to the repository root.
        abspath: Absolute filesystem path to the file.
        repo: Relationship back to the owning repository.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    relpath: Mapped[str] = mapped_column(String, nullable=False)
    abspath: Mapped[str] = mapped_column(String, nullable=False)

    repo: Mapped[Repository] = relationship(back_populates="files")


class Token(Base):
    __tablename__ = "tokens"

    """Token observed in source files.

    Attributes:
        id: Surrogate primary key.
        token: Token text (unique, original case).
        token_lc: Lowercased token for case-insensitive matching.
    """

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    token_lc: Mapped[str] = mapped_column(String, nullable=False)


Index("idx_tokens_token", Token.token)
Index("idx_tokens_token_lc", Token.token_lc)


class Posting(Base):
    __tablename__ = "postings"

    """Occurrence of a token in a file at a given position.

    The composite primary key ensures uniqueness of a posting.

    Attributes:
        file_id: Foreign key to ``files.id``.
        token_id: Foreign key to ``tokens.id``.
        line: 1-based line number where the token occurs.
        col: 1-based column where the token starts.
    """

    file_id: Mapped[int] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    token_id: Mapped[int] = mapped_column(
        ForeignKey("tokens.id", ondelete="CASCADE"), primary_key=True
    )
    line: Mapped[int] = mapped_column(primary_key=True)
    col: Mapped[int] = mapped_column(primary_key=True)


Index("idx_postings_token", Posting.token_id)
Index("idx_postings_file", Posting.file_id)


class Metadata(Base):
    __tablename__ = "metadata"

    """Key-value metadata for the index.

    Attributes:
        key: Primary key of the metadata entry.
        value: Associated value.
    """

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


def create_engine_for_path(db_path: Path) -> Engine:
    """Create a SQLAlchemy engine for a SQLite DB at the given path.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        A SQLAlchemy ``Engine`` configured for SQLite.
    """

    # Use POSIX path for SQLite URL on Windows too (e.g., C:/...)
    url_path = Path(db_path).resolve().as_posix()
    engine = create_engine(f"sqlite+pysqlite:///{url_path}", future=True)
    return engine


def init_db(engine: Engine) -> None:
    """Initialize database schema and pragmas using SQLAlchemy.

    Drops existing tables and recreates them to mirror the expected schema.

    Args:
        engine: SQLAlchemy engine bound to the target SQLite database.
    """

    # Apply pragmas similar to the original implementation
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")

    # Recreate schema
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
