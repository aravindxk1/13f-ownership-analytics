# Release Checklist — v0.1 (2026-10-03)

Scan of the release tree and its full git history (`git log -p --all`).

## Findings

1. Files over 1 MB: NONE (`find . -size +1M` excluding `.git` returns nothing).
2. Database / SEC payloads: NO `.duckdb`, `.xml`, `.zip` or `.env` files in the
   tree. No raw database rows and no downloaded SEC documents ship with this
   release. No vendor price series (no `adjusted_close` or price-series data).
3. Raw-row markers (`sshPrnamt`, `tableEntryTotal`): NO matches in any tracked file.
4. Personal paths and secrets: NO `/home/aravind`, `/mnt/d`, `D:\`, API keys,
   passwords or logins in any tracked file. The database path exists only as
   the `$HOLDINGS13F_DB` environment variable (`src/db.py`, `tests/conftest.py`).
5. Email addresses: exactly one — the public contact line in `README.md`
   (`Contact: aravindxk1@protonmail.com`). The same address appears as the
   commit author identity in git history (repo-local `user.name`/`user.email`
   config inside this repo only, required by git; the destination repo is private).
   No other personal text anywhere.
6. `__pycache__/` bytecode and `src/*.egg-info/` (editable-install artifacts from
   verification) exist on disk but are NOT tracked: pycache is gitignored, and
   the egg-info directory was deleted before committing.
7. `tests/data/edgar_expected.csv` and `tests/data/edgar_overrides.csv` are
   intentionally force-added (`git add -f`) although `*.csv` is gitignored: they
   are small (3.8 KB / 0.5 KB) expected-value fixtures with SEC URLs, not data
   dumps. No other CSV is tracked.
8. Tracked file list matches the allowlist (src/, tests/, README.md, LICENSE,
   pyproject.toml, .gitignore, CHANGELOG.md, docs/) — verified via
   `git log --name-only`. No scratch/, output/, history, or SEC files included.
9. Test verification (fresh virtualenv, `pip install -e .[dev]`, Python 3.10.12,
   duckdb 1.5.4, pandas 2.3.3, pytest 9.1.1):
   - `HOLDINGS13F_DB` unset: 22 passed, 13 skipped (all DB-backed tests skip
     with "HOLDINGS13F_DB is not set"), 0 failed.
   - `HOLDINGS13F_DB` set to the read-only database: 35 passed, 0 skipped,
     0 failed.
   - Remaining warnings only: pytest return-dict notices on the legacy
     dict-returning test helpers (benign; assertions still enforced).

## Fix applied during this review

- None required beyond items already actioned above (path sanitization,
  fixture force-adds, artifact deletion). Re-scanned after each fix; findings
  above are the final state.
