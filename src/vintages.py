"""Filing-vintage selection for 13F holdings.

Replaces one-row-per-grain version selection, which EDGAR evidence falsified:
- All six sampled amendments are full-snapshot restatements, not deltas.
- Grains without otherManager collapse distinct positions (24-row filings -> 1 row).
- Tie-breaks among equal-rank rows were frame-order dependent (13.6M-34.9M on
  identical 52-row input).

A vintage = all rows of one (manager_cik, report_period, filed_date,
is_amendment, source) filing submission. Per (manager, period) exactly one
winning vintage is kept WHOLE; there is no within-vintage dedupe (the DB lacks
otherManager, so identical-looking rows can be distinct positions).

Tie-break rule (documented): vintages order by (filed_date, is_amendment),
ties broken by min(source). Final output rows are stable-sorted by
(filed_date, is_amendment, shares, value_usd) for determinism.
"""
import csv
import pandas as pd

OVERLAP_THRESHOLD = 0.90
UNRESOLVED_STATUSES = frozenset({'ADDON_SUSPECT', 'MULTI_UNRESOLVED'})

_CACHE = {
    'vintages': None,      # DataFrame(manager_cik, report_period, filed_date, is_amendment, source, n_rows, shares)
    'overlap': None,       # DataFrame(manager_cik, report_period, n_orig, n_over, n_amd)
    'overrides': None,     # dict[(cik, period)] -> {filed_date, accession, evidence_url, quote}
    'decisions': {},       # (cik, period) -> decision dict
    'periods': None,
}


def overlap_status(n_overlap, n_original):
    """Classify an amendment vintage: RESTATEMENT if it covers >=90% of the
    original's CUSIPs, else ADDON_SUSPECT. Empty original counts as restatement."""
    if not n_original:
        return 'RESTATEMENT'
    return 'RESTATEMENT' if (n_overlap / n_original) >= OVERLAP_THRESHOLD else 'ADDON_SUSPECT'


def load_overrides(path):
    """Load tests/data/edgar_overrides.csv into {(cik, period): entry}."""
    out = {}
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            out[(row['manager_cik'].strip(), row['report_period'].strip())] = {
                'filed_date': row['filed_date'].strip(),
                'accession': row['winning_accession'].strip(),
                'evidence_url': row['evidence_url'].strip(),
                'quote': row['quote'].strip(),
            }
    _CACHE['overrides'] = out
    return out


def preload(conn, periods, overrides_path=None):
    """Full (re)load of vintage + CUSIP-overlap tables for the given report periods."""
    _CACHE['vintages'] = None
    _CACHE['overlap'] = None
    _CACHE['decisions'] = {}
    _CACHE['periods'] = None
    if overrides_path:
        load_overrides(overrides_path)
    elif _CACHE['overrides'] is None:
        _CACHE['overrides'] = {}
    ensure_loaded(conn, periods)


def ensure_loaded(conn, periods):
    """Load any not-yet-cached report periods, merging into the cache."""
    from pathlib import Path as _Path
    if _CACHE['overrides'] is None:
        default = _Path(__file__).parent.parent / 'tests' / 'data' / 'edgar_overrides.csv'
        _CACHE['overrides'] = load_overrides(str(default)) if default.exists() else {}
    periods = [str(p) for p in periods]
    have = set(_CACHE['periods'] or [])
    missing = [p for p in periods if p not in have]
    if not missing:
        return
    ph = ','.join(['?'] * len(missing))
    vint = conn.execute(
        f"SELECT manager_cik, report_period, filed_date, is_amendment, source, "
        f"COUNT(*) AS n_rows, SUM(shares) AS shares "
        f"FROM main.institutional_holdings WHERE report_period IN ({ph}) "
        f"GROUP BY 1,2,3,4,5",
        missing,
    ).df()
    vint['report_period'] = vint['report_period'].astype(str)
    vint['filed_date'] = vint['filed_date'].astype(str)
    over = conn.execute(
        f"""WITH o AS (SELECT DISTINCT manager_cik, report_period, cusip
                        FROM main.institutional_holdings
                        WHERE is_amendment = 0 AND report_period IN ({ph})),
                 a AS (SELECT DISTINCT manager_cik, report_period, cusip
                        FROM main.institutional_holdings
                        WHERE is_amendment = 1 AND report_period IN ({ph})),
                 no AS (SELECT manager_cik, report_period, COUNT(*) AS n_orig FROM o GROUP BY 1,2),
                 na AS (SELECT manager_cik, report_period, COUNT(*) AS n_amd FROM a GROUP BY 1,2),
                 ov AS (SELECT o.manager_cik, o.report_period, COUNT(*) AS n_over
                        FROM o JOIN a USING (manager_cik, report_period, cusip) GROUP BY 1,2)
            SELECT no.manager_cik, CAST(no.report_period AS VARCHAR) AS report_period,
                   no.n_orig, COALESCE(ov.n_over, 0) AS n_over, na.n_amd
            FROM no LEFT JOIN ov USING (manager_cik, report_period)
                    LEFT JOIN na USING (manager_cik, report_period)""",
        missing + missing,
    ).df()
    if _CACHE['overlap'] is None:
        _CACHE['overlap'] = over
    else:
        _CACHE['overlap'] = pd.concat([_CACHE['overlap'], over], ignore_index=True)
    if _CACHE['vintages'] is None:
        _CACHE['vintages'] = vint
    else:
        _CACHE['vintages'] = pd.concat([_CACHE['vintages'], vint], ignore_index=True)
    _CACHE['decisions'] = {}
    _CACHE['periods'] = sorted(set(_CACHE['periods'] or []) | set(periods))
    return vint


def _latest(vintages):
    """Latest vintage by (filed_date, is_amendment), ties by min(source). Deterministic."""
    ordered = sorted(vintages, key=lambda v: (v['filed_date'], v['is_amendment'], v['source']))
    top_date = ordered[-1]['filed_date']
    top_amd = max(v['is_amendment'] for v in vintages if v['filed_date'] == top_date)
    cands = [v for v in vintages if v['filed_date'] == top_date and v['is_amendment'] == top_amd]
    return sorted(cands, key=lambda v: v['source'])[0]


def get_decision(cik, period):
    """Winning vintage + status for one (manager_cik, report_period).

    Statuses: SINGLE (one vintage, or several original-only vintages -> latest wins),
    RESTATEMENT (one amendment vintage covering >=90% of original CUSIPs),
    ADDON_SUSPECT (<90% — UNRESOLVED, fallback = latest vintage, flagged),
    MULTI_UNRESOLVED (>1 amendment vintage, no override — fallback = latest, flagged),
    OVERRIDE (multi-amendment resolved by tests/data/edgar_overrides.csv filed_date match).
    stake_shares = raw shares in non-winning vintages.
    """
    key = (str(cik), str(period))
    if key in _CACHE['decisions']:
        return _CACHE['decisions'][key]
    vint = _CACHE['vintages']
    if vint is None:
        raise RuntimeError("vintages.preload() must run before get_decision()")
    rows = vint[(vint['manager_cik'] == key[0]) & (vint['report_period'] == key[1])]
    if rows.empty:
        _CACHE['decisions'][key] = None
        return None
    vs = rows.to_dict('records')
    total = sum(int(r['shares'] or 0) for r in vs)

    def _stake(winner):
        return total - int(winner['shares'] or 0)

    if len(vs) == 1:
        d = {'status': 'SINGLE', 'winner': vs[0], 'stake_shares': 0, 'overlap': None}
        _CACHE['decisions'][key] = d
        return d
    amd = [v for v in vs if int(v['is_amendment']) == 1]
    if len(amd) > 1:
        ov = (_CACHE['overrides'] or {}).get(key)
        if ov:
            match = [v for v in amd if str(v['filed_date']) == ov['filed_date']]
            if match:
                w = match[0]
                d = {'status': 'OVERRIDE', 'winner': w, 'stake_shares': _stake(w),
                     'overlap': None, 'override': ov}
                _CACHE['decisions'][key] = d
                return d
        w = _latest(vs)
        d = {'status': 'MULTI_UNRESOLVED', 'winner': w, 'stake_shares': _stake(w), 'overlap': None}
        _CACHE['decisions'][key] = d
        return d
    if len(amd) == 1:
        o = _CACHE['overlap']
        hit = o[(o['manager_cik'] == key[0]) & (o['report_period'] == key[1])]
        n_over = int(hit['n_over'].iloc[0]) if not hit.empty else 0
        n_orig = int(hit['n_orig'].iloc[0]) if not hit.empty else 0
        status = overlap_status(n_over, n_orig)
        w = amd[0] if status == 'RESTATEMENT' else _latest(vs)
        d = {'status': status, 'winner': w, 'stake_shares': _stake(w),
             'overlap': {'n_overlap': n_over, 'n_original': n_orig}}
        _CACHE['decisions'][key] = d
        return d
    w = _latest(vs)
    d = {'status': 'SINGLE', 'winner': w, 'stake_shares': _stake(w), 'overlap': None}
    _CACHE['decisions'][key] = d
    return d


def universe_stats():
    """Counts over manager-periods WITH amendment vintages: restatement,
    add-on-suspect, multi-unresolved, override. Plus total amd-pairs and singles."""
    vint = _CACHE['vintages']
    stats = {'pairs_with_amendments': 0, 'restatement': 0, 'addon_suspect': 0,
             'multi_unresolved': 0, 'override': 0, 'unresolved': 0}
    if vint is None or vint.empty:
        return stats
    for (cik, period), g in vint.groupby(['manager_cik', 'report_period']):
        n_amd_vintages = int(((g['is_amendment'].astype(int) == 1)).sum())
        if n_amd_vintages == 0:
            continue
        stats['pairs_with_amendments'] += 1
        d = get_decision(cik, period)
        if d['status'] == 'RESTATEMENT':
            stats['restatement'] += 1
        elif d['status'] == 'ADDON_SUSPECT':
            stats['addon_suspect'] += 1
        elif d['status'] == 'MULTI_UNRESOLVED':
            stats['multi_unresolved'] += 1
        elif d['status'] == 'OVERRIDE':
            stats['override'] += 1
    stats['unresolved'] = stats['addon_suspect'] + stats['multi_unresolved']
    return stats
