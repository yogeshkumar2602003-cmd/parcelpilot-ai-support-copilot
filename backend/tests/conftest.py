from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.db import get_connection, init_schema
from app.domain.repositories import Repositories
from app.ingestion.loader import run_ingestion
from app.retrieval.retriever import build_index

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def conn(tmp_path):
    """A fresh, fully-ingested in-memory-equivalent SQLite DB per test."""
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_schema(c)
    run_ingestion(c, DATA_DIR)
    build_index(c)
    yield c
    c.close()


@pytest.fixture()
def repos(conn):
    return Repositories.build(conn)


@pytest.fixture()
def snapshot_now():
    from app.domain.snapshot import get_snapshot
    return get_snapshot()
