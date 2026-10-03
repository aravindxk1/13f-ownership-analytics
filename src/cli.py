"""13F Institutional Ownership Analytics - CLI."""
import sys
import json
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection
from snapshot import build_ownership_snapshot
from changes import compare_quarters, get_largest_changes
from report import (save_snapshot_csv, save_top10_csv, save_changes_csv,
                    save_concentration_csv, generate_final_report,
                    compute_shares_outstanding_ratios)
import vintages

TICKERS = ['AAPL', 'MSFT', 'NVDA']
BASE_PERIOD = '2025-12-31'
COMPARE_PERIODS = ['2025-09-30', '2025-06-30', '2025-03-31', '2024-12-31']
ALL_PERIODS = [BASE_PERIOD] + COMPARE_PERIODS
OVERRIDES_PATH = str(Path(__file__).parent.parent / "tests" / "data" / "edgar_overrides.csv")


def build_snapshot_cache(conn):
    """Build ownership snapshots for every ticker/period once; shared by phases."""
    cache = {}
    for ticker in TICKERS:
        for period in ALL_PERIODS:
            print(f"  snapshot {ticker} @ {period}")
            cache[(ticker, period)] = build_ownership_snapshot(conn, ticker, period)
    return cache

def run_phase1(conn):
    """Phase 1: Ownership snapshots."""
    print("=" * 60)
    print("PHASE 1: Ownership Snapshots")
    print("=" * 60)

    all_snapshots = {}
    for ticker in TICKERS:
        print(f"\n--- {ticker} @ {BASE_PERIOD} ---")
        snap = build_ownership_snapshot(conn, ticker, BASE_PERIOD)
        if snap['metrics']:
            all_snapshots[ticker] = snap
            m = snap['metrics']
            print(f"  Holders: {m['n_holders']:,}")
            print(f"  Total shares: {m['total_shares']:,.0f}")
            print(f"  Total value: ${m['total_value_dollars']:,.0f}")
            print(f"  Top-5 conc: {m['top5_share_concentration']:.1%}")
            print(f"  HHI: {m['hhi']:.4f}")
            print(f"  Top holder: {m['top1_holder']}")

            save_snapshot_csv(snap, ticker, BASE_PERIOD)
            save_top10_csv(snap, ticker, BASE_PERIOD)
        else:
            print(f"  No data for {ticker}")

    return all_snapshots

def run_phase2(conn, all_snapshots):
    """Phase 2: Quarterly ownership changes."""
    print("\n" + "=" * 60)
    print("PHASE 2: Quarterly Ownership Changes")
    print("=" * 60)

    all_changes = {}
    for ticker in TICKERS:
        if ticker not in all_snapshots:
            continue
        for from_period in COMPARE_PERIODS:
            print(f"\n--- {ticker}: {from_period} → {BASE_PERIOD} ---")
            changes = compare_quarters(conn, ticker, from_period, BASE_PERIOD)
            if not changes.empty:
                all_changes[(ticker, from_period, BASE_PERIOD)] = changes
                n_new = (changes['status'] == 'NEW').sum()
                n_exit = (changes['status'] == 'EXIT').sum()
                n_inc = (changes['status'] == 'INCREASE').sum()
                n_red = (changes['status'] == 'REDUCTION').sum()
                print(f"  New: {n_new}, Exits: {n_exit}, Increases: {n_inc}, Reductions: {n_red}")
                save_changes_csv(changes, ticker, from_period, BASE_PERIOD)
            else:
                print(f"  No data")

    return all_changes

def run_concentration_trends(cache):
    """Concentration trends derived from the shared snapshot cache (no rebuilds)."""
    print("\n" + "=" * 60)
    print("CONCENTRATION TRENDS")
    print("=" * 60)

    all_concentration = {}
    for ticker in TICKERS:
        print(f"\n--- {ticker} ---")
        rows = []
        for period in ALL_PERIODS:
            snap = cache[(ticker, period)]
            if snap['metrics']:
                m = snap['metrics']
                rows.append({
                    'report_period': period,
                    'n_holders': m['n_holders'],
                    'total_shares': m['total_shares'],
                    'total_value_dollars': m['total_value_dollars'],
                    'top5_concentration': m['top5_share_concentration'],
                    'top10_concentration': m['top10_share_concentration'],
                    'hhi': m['hhi'],
                })
        conc = pd.DataFrame(rows)
        if not conc.empty:
            all_concentration[ticker] = conc
            save_concentration_csv(conc, ticker)
            for _, row in conc.iterrows():
                print(f"  {row['report_period']}: {row['n_holders']:,} holders, HHI {row['hhi']:.4f}")

    return all_concentration


def build_quarterly_closure(conn, cache):
    """Corrected common totals for 2025-03-31..2025-12-31 + shares-outstanding ratio each quarter."""
    closure = []
    for ticker in TICKERS:
        quarters = ['2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31']
        totals = {q: cache[(ticker, q)]['metrics']['total_shares'] for q in quarters}
        ratios = {}
        for q in quarters:
            try:
                r = compute_shares_outstanding_ratios(conn, {ticker: cache[(ticker, q)]}, q)
                ratios[q] = f"{r[ticker]['ratio']:.1%}"
            except RuntimeError as e:
                ratios[q] = f"ERROR: {e}"
        dip = int((totals['2025-06-30'] + totals['2025-12-31']) / 2 - totals['2025-09-30'])
        closure.append({
            'ticker': ticker,
            '2025-03-31': totals['2025-03-31'], '2025-06-30': totals['2025-06-30'],
            '2025-09-30': totals['2025-09-30'], '2025-12-31': totals['2025-12-31'],
            'dip_remaining': dip,
            'ratios': ' / '.join(ratios[q] for q in quarters),
        })
    pd.DataFrame(closure).to_csv(
        Path(__file__).parent.parent / "scratch" / "phase3_closure.csv", index=False)
    return closure

def write_audit_csvs(all_snapshots):
    """Write row-level audit output to scratch/ (not committed). Report shows aggregates only."""
    scratch = Path(__file__).parent.parent / "scratch" / "phase2_audit"
    scratch.mkdir(parents=True, exist_ok=True)
    cols = ['manager_cik', 'manager_name', 'report_period', 'filed_date', 'cusip',
            'share_class', 'put_call', 'bucket', 'discretion', 'shares', 'value_dollars',
            'value_usd', 'is_amendment', 'source', 'value_scale_corrected',
            'contracts_normalized', 'suspect']
    for ticker, snap in all_snapshots.items():
        for name in ('audit_scaled', 'audit_contracts', 'audit_suspect'):
            frame = snap.get(name)
            if frame is not None and not frame.empty:
                keep = [c for c in cols if c in frame.columns]
                frame[keep].to_csv(scratch / f"{ticker}_{snap['report_period']}_{name}.csv", index=False)
    recon_rows = []
    for ticker, snap in all_snapshots.items():
        row = {'ticker': ticker, **snap['recon']}
        recon_rows.append(row)
    pd.DataFrame(recon_rows).to_csv(scratch / "reconciliation_by_ticker.csv", index=False)
    return scratch


def run_all(test_results=None):
    """Run full analysis pipeline."""
    conn = get_connection()
    try:
        vintages.preload(conn, ALL_PERIODS, OVERRIDES_PATH)
        print(f"Vintage preload: {len(vintages._CACHE['vintages']):,} vintages; "
              f"overrides: {len(vintages._CACHE['overrides'])}")
        cache = build_snapshot_cache(conn)
        all_snapshots = {t: cache[(t, BASE_PERIOD)] for t in TICKERS}
        for ticker in TICKERS:
            m = all_snapshots[ticker]['metrics']
            print(f"{ticker}: holders={m['n_holders']:,} shares={m['total_shares']:,} "
                  f"unresolved={m['unresolved_n_managers']} stake={m['unresolved_stake_shares']:,}")
            save_snapshot_csv(all_snapshots[ticker], ticker, BASE_PERIOD)
            save_top10_csv(all_snapshots[ticker], ticker, BASE_PERIOD)
        all_changes = run_phase2(conn, all_snapshots)
        all_concentration = run_concentration_trends(cache)
        closure = build_quarterly_closure(conn, cache)
        stats = vintages.universe_stats()
        print(f"Vintage stats: {stats}")

        if test_results is None:
            tests_dir = Path(__file__).parent.parent / "tests"
            sys.path.insert(0, str(tests_dir))
            from test_analytics import run_all_tests
            test_results = run_all_tests(conn)
            n_pass = sum(1 for r in test_results.values() if r['passed'])
            print(f"\nTests: {n_pass}/{len(test_results)} passed")

        write_audit_csvs(all_snapshots)
        report_path = generate_final_report(all_snapshots, all_changes, all_concentration, test_results, conn,
                                            universe_stats=stats, quarterly_closure=closure)
        from report import verify_rendered_report
        render_results = verify_rendered_report(report_path)
        all_results = {**test_results, **render_results}
        n_all = len(all_results)
        n_all_pass = sum(1 for r in all_results.values() if r['passed'])
        print(f"\nFULL TEST LIST ({n_all_pass}/{n_all} passed):")
        for name, result in all_results.items():
            status = "PASS" if result['passed'] else "FAIL"
            print(f"  {name}: {status} - {result['details']}")
        report_path = generate_final_report(all_snapshots, all_changes, all_concentration, all_results, conn,
                                            universe_stats=stats, quarterly_closure=closure)
        print(f"\n{'=' * 60}")
        print(f"Report generated: {report_path}")
        print(f"{'=' * 60}")
    finally:
        conn.close()

if __name__ == '__main__':
    run_all()
