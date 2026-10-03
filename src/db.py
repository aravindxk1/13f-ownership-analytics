"""13F Institutional Ownership Analytics - Database Access Module."""
import os
import duckdb


def _db_path():
    path = os.environ.get("HOLDINGS13F_DB", "")
    if not path:
        raise RuntimeError(
            "HOLDINGS13F_DB is not set. Set it to the read-only DuckDB file path, e.g. "
            'HOLDINGS13F_DB="/path/to/canonical.duckdb". The database is never modified.'
        )
    return path


def get_connection():
    """Return read-only DuckDB connection (path from HOLDINGS13F_DB)."""
    return duckdb.connect(_db_path(), read_only=True)

def execute_query(conn, sql, params=None):
    """Execute read-only query and return results."""
    if params:
        return conn.execute(sql, params).fetchall()
    return conn.execute(sql).fetchall()

def execute_df(conn, sql, params=None):
    """Execute read-only query and return pandas DataFrame."""
    if params:
        return conn.execute(sql, params).df()
    return conn.execute(sql).df()
