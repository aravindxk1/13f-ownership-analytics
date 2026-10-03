"""Pytest fixtures for the 13F pipeline test suite.

Tests that take the `conn` fixture need the read-only DuckDB file pointed to by
HOLDINGS13F_DB. When the variable is unset they SKIP with a clear message instead
of failing. The database is only ever opened read-only.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def conn():
    db = os.environ.get("HOLDINGS13F_DB", "")
    if not db:
        pytest.skip("HOLDINGS13F_DB is not set — DB-backed test skipped")
    import duckdb

    c = duckdb.connect(db, read_only=True)
    try:
        yield c
    finally:
        c.close()
