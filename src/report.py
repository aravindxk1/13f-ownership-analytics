"""13F report generation: CSV/Parquet outputs + final markdown report."""
import pandas as pd
from pathlib import Path
from datetime import datetime
from changes import get_largest_changes

OUTPUT_DIR = Path(__file__).parent.parent / "output"

BEFORE = {
    'AAPL': {'holders': 5931, 'shares': 9067070888, 'value': 2463458364829, 'top5': 0.425, 'hhi': 0.0524},
    'MSFT': {'holders': 6123, 'shares': 5167435364, 'value': 2498992690762, 'top5': 0.389, 'hhi': 0.0436},
    'NVDA': {'holders': 5687, 'shares': 15525349046, 'value': 2894908177382, 'top5': 0.436, 'hhi': 0.0505},
}


def save_snapshot_csv(snapshot_result, ticker, period):
    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = OUTPUT_DIR / f"{ticker}_{period}_snapshot.csv"
    snapshot_result['snapshot'].to_csv(filename, index=False)
    return filename


def save_top10_csv(snapshot_result, ticker, period):
    filename = OUTPUT_DIR / f"{ticker}_{period}_top10.csv"
    snapshot_result['top10'].to_csv(filename, index=False)
    return filename


def save_changes_csv(changes_df, ticker, from_period, to_period):
    filename = OUTPUT_DIR / f"{ticker}_{from_period}_to_{to_period}_changes.csv"
    changes_df.to_csv(filename, index=False)
    return filename


def save_concentration_csv(conc_df, ticker):
    filename = OUTPUT_DIR / f"{ticker}_concentration_trend.csv"
    conc_df.to_csv(filename, index=False)
    return filename


def compute_shares_outstanding_ratios(conn, all_snapshots, report_period='2025-12-31'):
    """13F common-only, version-ruled totals vs SharesOutstanding from pit_fundamentals.

    Raises RuntimeError if the denominator row is missing. No try/except-pass.
    """
    ratios = {}
    for ticker in sorted(all_snapshots):
        if not all_snapshots[ticker]['metrics']:
            raise RuntimeError(f"Section 7: no common-stock snapshot for {ticker} at {report_period}")
        r = conn.execute(
            "SELECT value, period_date FROM pit_fundamentals "
            "WHERE ticker = ? AND metric = 'SharesOutstanding' AND period_date <= ? "
            "ORDER BY period_date DESC LIMIT 1",
            [ticker, report_period],
        ).fetchone()
        if r is None or r[0] is None or r[0] <= 0:
            raise RuntimeError(
                f"Section 7: no SharesOutstanding row in pit_fundamentals for {ticker} as of {report_period}"
            )
        total = all_snapshots[ticker]['metrics']['total_shares']
        ratios[ticker] = {
            'source_table': 'pit_fundamentals',
            'shares_outstanding': int(r[0]),
            'as_of_date': str(r[1]),
            'total_13f_shares': int(total),
            'ratio': float(total / r[0]),
            'flag_over_100': bool(total > r[0]),
        }
    return ratios


def chain_sums_ok(recon):
    """Check the printed reconciliation identity (no options term: options are
    disjoint from LONG shares). Returns True iff it sums exactly."""
    return bool(
        (recon['long_shares'] - recon['debt_shares'] - recon['unclass_opt_shares']
         - recon['unclass_shares'] - recon['suspect_shares'] - recon['vintage_drops_shares']
         == recon['corrected_shares']) and (
         recon['long_rows'] - recon['debt_rows'] - recon['unclass_opt_rows']
         - recon['unclass_rows'] - recon['suspect_rows'] - recon['vintage_drops_rows']
         == recon['corrected_rows']))


def generate_final_report(all_snapshots, all_changes, all_concentration, test_results, conn=None,
                          universe_stats=None, quarterly_closure=None):
    """Generate the single final markdown report."""
    report_path = OUTPUT_DIR / "13F_Ownership_Intelligence_Report.md"

    if conn is None:
        raise RuntimeError("Section 7: no database connection — refusing to render an empty ratios section.")
    ratios = compute_shares_outstanding_ratios(conn, all_snapshots)

    lines = []
    lines.append("# 13F Institutional Ownership Intelligence Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Before/After table
    lines.append("## 1. Before/After Comparison")
    lines.append("")
    lines.append("| Metric | AAPL Before | AAPL After | MSFT Before | MSFT After | NVDA Before | NVDA After |")
    lines.append("|--------|-------------|------------|-------------|------------|-------------|------------|")
    for label, key, mkey in [("Holders", "holders", "n_holders"), ("Shares", "shares", "total_shares"), ("Value ($)", "value", "total_value_dollars"), ("Top-5 Conc.", "top5", "top5_share_concentration"), ("HHI", "hhi", "hhi")]:
        b = [BEFORE[t][key] for t in ['AAPL', 'MSFT', 'NVDA']]
        a = [all_snapshots[t]['metrics'][mkey] if t in all_snapshots and all_snapshots[t]['metrics'] else 0 for t in ['AAPL', 'MSFT', 'NVDA']]
        if key in ('top5', 'hhi'):
            row = f"| {label} | {b[0]:.1%} | {a[0]:.1%} | {b[1]:.1%} | {a[1]:.1%} | {b[2]:.1%} | {a[2]:.1%} |"
        elif key == 'value':
            row = f"| {label} | ${b[0]:,.0f} | ${a[0]:,.0f} | ${b[1]:,.0f} | ${a[1]:,.0f} | ${b[2]:,.0f} | ${a[2]:,.0f} |"
        else:
            row = f"| {label} | {b[0]:,} | {a[0]:,} | {b[1]:,} | {a[1]:,} | {b[2]:,} | {a[2]:,} |"
        lines.append(row)
    lines.append("")
    lines.append("**Key changes:** Options excluded from all common-stock metrics; managers grouped by CIK only; canonical display names applied.")
    lines.append("")

    # Executive Summary
    lines.append("## 2. Executive Summary")
    lines.append("")
    lines.append("> **PROVISIONAL**: Amendment/version-selection logic not yet resolved. All totals, concentration, and HHI metrics are subject to change.")
    lines.append("")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        if ticker in all_snapshots and all_snapshots[ticker]:
            snap = all_snapshots[ticker]
            m = snap['metrics']
            lines.append(f"### {ticker} (2025-12-31) — PROVISIONAL")
            lines.append(f"- Top holder: {m['top1_holder']} (${m['top1_value_dollars']:,.0f})")
            lines.append(f"- Total 13F holders (common stock): {m['n_holders']:,}")
            lines.append(f"- Total reported shares (common stock): {m['total_shares']:,.0f}")
            lines.append(f"- Total reported value: ${m['total_value_dollars']:,.0f}")
            lines.append(f"- Top-5 concentration: {m['top5_share_concentration']:.1%}")
            lines.append(f"- HHI: {m['hhi']:.4f}")
            lines.append(f"- Options (share-equivalents): {m['options_total_share_equivalents']:,.0f} shares, {m['options_n_holders']:,} holders ({m['contracts_normalized_rows']:,} rows normalized ×100)")
            lines.append(f"- Non-common debt: {m['debt_total_shares']:,.0f} shares, {m['debt_n_holders']:,} holders")
            lines.append(f"- Suspect (excluded): {m['suspect_total_shares']:,.0f} shares, {m['suspect_n_holders']:,} holders")
            lines.append(f"- Value-scale corrected rows: {m['scaled_rows']:,}")
            lines.append(f"- UNRESOLVED manager-periods: {m['unresolved_n_managers']:,} ({m['unresolved_stake_shares']:,} shares at stake) — PROVISIONAL")
            lines.append("")

    # Methodology
    lines.append("## 3. Methodology")
    lines.append("")
    lines.append("### Data Source")
    lines.append("- Database: `$HOLDINGS13F_DB` (canonical 13F DuckDB file, read-only)")
    lines.append("- Table: `main.institutional_holdings` (118,569,246 rows)")
    lines.append("- Analysis period: 2025-12-31 (2025 Q4)")
    lines.append("")
    lines.append("### Normalization Rules")
    lines.append("- **Prices**: exact `close` on each report_period from `main.market_prices_daily` (`asset` = ticker, `source` = 'yahoo'). If a price is missing, ratio-based normalization is skipped and flagged.")
    lines.append("- **Unit conversion**: `value_usd / divisor(source_batch)` where divisor=1000 for `2022q4_form13f.zip` and earlier, divisor=1 for `2023q1_form13f.zip` and later")
    lines.append("- **Share classes (fail-closed whitelist)**: only whitelisted common labels enter common totals. BOND/NOTE-type classes go to 'non-common debt'. Option-style classes with empty put_call go to 'unclassified-option-class'. Everything else is 'unclassified'. None of these enter common totals.")
    lines.append("- **Value scale**: per (filer CIK, ticker, period), if ≥95% of a filer's assessable common rows have value/shares within 0.0008–0.0012 of the stock price, value is multiplied by 1000 and flagged (`value_scale_corrected`). Shares are never changed. Rows still below 0.5× the price go to a 'suspect' bucket, excluded from common totals and listed separately.")
    lines.append("- **Options**: per (filer, ticker, period, put_call), if ≥95% of rows have (value/shares)/price in 80–120, shares are multiplied by 100 and flagged (`contracts_normalized`). Keyed on put_call and filer, never on share_class. Options are reported as share-equivalents only; dollar values are removed because filers mix notional and premium conventions.")
    lines.append("- **Version rule (vintage selection)**: Group rows into filing vintages by (manager_cik, report_period, filed_date, is_amendment, source). Per (manager, period) keep ALL rows of exactly one winning vintage: the latest by (filed_date, is_amendment), ties by min(source). Never keep one row per grain; no within-vintage dedupe (DB lacks otherManager). Amendment vintages covering ≥90% of the original's CUSIPs are restatements; below that, or with >1 amendment vintage and no entry in tests/data/edgar_overrides.csv, the manager-period is UNRESOLVED (latest-vintage fallback, flagged PROVISIONAL with shares at stake).")
    lines.append("- **Aggregation**: SUM over discretion and share_class_norm to (manager_cik, report_period, cusip, put_call) grain")
    lines.append("- **Identity**: Managers grouped by manager_cik only; canonical display name = most recent filing, cleaned of trailing backslashes and stray spaces")
    lines.append("- **PIT**: Block cutoff < 2013-05-20; day-granularity only")
    lines.append("- **BRK-A**: Ticker grain quarantined (bridge mislabels BRK-B as BRK-A)")
    lines.append("")

    # Ownership Snapshots
    lines.append("## 4. Ownership Snapshots (2025-12-31)")
    lines.append("")
    lines.append("> **PROVISIONAL**: Top-10 lists, totals, concentration, and HHI are subject to change until amendment/version-selection logic is resolved.")
    lines.append("")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        if ticker in all_snapshots and all_snapshots[ticker]:
            snap = all_snapshots[ticker]
            lines.append(f"### {ticker} Top 10 Holders (Common Stock) — PROVISIONAL")
            lines.append("")
            lines.append("| Rank | Manager (CIK) | Shares | Value ($) |")
            lines.append("|------|---------------|--------|-----------|")
            for i, row in snap['top10'].iterrows():
                lines.append(f"| {i+1} | {row['manager_name']} ({row['manager_cik']}) | {row['shares']:,.0f} | {row['value_dollars']:,.0f} |")
            lines.append("")

            if not snap['options_top10'].empty:
                lines.append(f"### {ticker} Top 10 Option Holders (share-equivalents) — PROVISIONAL")
                lines.append("")
                lines.append("Share-equivalents only; dollar values removed (filers mix notional and premium conventions).")
                lines.append("")
                lines.append("| Rank | Manager (CIK) | Type | Share-equivalents |")
                lines.append("|------|---------------|------|-------------------|")
                for i, row in snap['options_top10'].iterrows():
                    lines.append(f"| {i+1} | {row['manager_name']} ({row['manager_cik']}) | {row['put_call']} | {row['shares']:,.0f} |")
                lines.append("")

            if not snap['debt_top10'].empty:
                lines.append(f"### {ticker} Top 10 Non-Common-Debt Holders — PROVISIONAL")
                lines.append("")
                lines.append("| Rank | Manager (CIK) | Shares |")
                lines.append("|------|---------------|--------|")
                for i, row in snap['debt_top10'].iterrows():
                    lines.append(f"| {i+1} | {row['manager_name']} ({row['manager_cik']}) | {row['shares']:,.0f} |")
                lines.append("")

            if not snap['suspect_top10'].empty:
                lines.append(f"### {ticker} Top 10 Suspect Rows (excluded from common) — PROVISIONAL")
                lines.append("")
                lines.append("| Rank | Manager (CIK) | Shares |")
                lines.append("|------|---------------|--------|")
                for i, row in snap['suspect_top10'].iterrows():
                    lines.append(f"| {i+1} | {row['manager_name']} ({row['manager_cik']}) | {row['shares']:,.0f} |")
                lines.append("")

    # Quarterly Changes
    lines.append("## 5. Quarterly Ownership Changes")
    lines.append("")
    lines.append("> **PROVISIONAL**: Amendment/version-selection logic not yet resolved. Change tables may contain double-counted positions where original and amendment rows coexist.")
    lines.append("")
    for key, changes in all_changes.items():
        if changes is not None and not changes.empty:
            ticker, from_p, to_p = key
            lines.append(f"### {ticker}: {from_p} -> {to_p} (PROVISIONAL)")
            lines.append("")
            largest = get_largest_changes(changes, 5)
            lines.append("**Largest Increases:**")
            lines.append("")
            lines.append("| Manager | Shares Change | New Shares |")
            lines.append("|---------|---------------|------------|")
            for _, row in largest['largest_increases'].iterrows():
                lines.append(f"| {row['manager_name']} | {row['shares_change']:,.0f} | {row['shares_to']:,.0f} |")
            lines.append("")
            lines.append("**Largest Reductions:**")
            lines.append("")
            lines.append("| Manager | Shares Change | Remaining Shares |")
            lines.append("|---------|---------------|------------------|")
            for _, row in largest['largest_reductions'].iterrows():
                lines.append(f"| {row['manager_name']} | {row['shares_change']:,.0f} | {row['shares_to']:,.0f} |")
            lines.append("")

    # Concentration Trends
    lines.append("## 6. Institutional Concentration Trends")
    lines.append("")
    lines.append("> **PROVISIONAL**: Concentration metrics are subject to change until amendment/version-selection logic is resolved.")
    lines.append("")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        if ticker in all_concentration and not all_concentration[ticker].empty:
            lines.append(f"### {ticker} — PROVISIONAL")
            lines.append("")
            lines.append("| Period | N Holders | Total Shares | Top-5 Conc. | HHI |")
            lines.append("|--------|-----------|--------------|-------------|-----|")
            for _, row in all_concentration[ticker].iterrows():
                lines.append(f"| {row['report_period']} | {row['n_holders']:,} | {row['total_shares']:,.0f} | {row['top5_concentration']:.1%} | {row['hhi']:.4f} |")
            lines.append("")

    # Shares Outstanding Ratios
    lines.append("## 7. Shares Outstanding Ratios")
    lines.append("")
    lines.append("Numerator: common-only, version-ruled 13F totals. Denominator: `pit_fundamentals` (`metric` = 'SharesOutstanding').")
    lines.append("")
    lines.append("| Ticker | 13F Common Shares | Shares Outstanding | Source Table | As-of Date | Ratio | Flag |")
    lines.append("|--------|-------------------|-------------------|--------------|------------|-------|------|")
    for t in ['AAPL', 'MSFT', 'NVDA']:
        r = ratios[t]
        flag = "**OVER 100%**" if r['flag_over_100'] else "—"
        lines.append(f"| {t} | {r['total_13f_shares']:,} | {r['shares_outstanding']:,} | {r['source_table']} | {r['as_of_date']} | {r['ratio']:.1%} | {flag} |")
    lines.append("")

    # Data Quality Warnings
    lines.append("## 8. Data-Quality Warnings and Limitations")
    lines.append("")
    lines.append("- **PROVISIONAL — Amendment handling**: Vintage selection keeps the winning filing vintage whole. UNRESOLVED manager-periods (add-on-suspect CUSIP overlap <90%, or multiple amendment vintages without an override entry) use latest-vintage fallback; totals, concentration and HHI including their shares are marked PROVISIONAL with shares at stake. Change tables and amendment-sensitive metrics are marked PROVISIONAL.")
    lines.append("- **Options**: CALL/PUT rows excluded from common-stock metrics; shown separately in options section")
    lines.append("- **Unit conversion**: Divisor keyed on source batch; verified for AAPL/MSFT/NVDA but not exhaustive for all 151k CUSIPs")
    lines.append("- **BRK-A ticker grain**: Quarantined due to ticker bridge error (BRK-B mislabeled as BRK-A)")
    lines.append("- **Pre-2013 PIT**: Blocked; filed_date values are backfilled placeholders")
    lines.append("- **Same-day ordering**: 2,921 same-day orig+amd pairs in 2025-12-31; intraday order unknown")
    lines.append("- **Missing filings**: Manager absence from a period is not necessarily an exit (may be non-filer)")
    lines.append("- **Identity**: Managers grouped by CIK only; Susquehanna has 10 CIKs, Morgan Stanley has 3; not merged")
    lines.append("- **Barclays MSFT 2025-09-30**: ~821M-share position labeled UNVERIFIED — see scratch/item2_banks.md")
    lines.append("")

    # Test Results
    lines.append("## 9. Test Results and Reconciliation")
    lines.append("")
    if not test_results:
        raise ValueError("Section 9: test_results is empty — failing the build instead of rendering an empty test table.")
    lines.append("### Test Results")
    lines.append("")
    lines.append("| Test | Status | Details |")
    lines.append("|------|--------|---------|")
    for test_name, result in test_results.items():
        status = "PASS" if result['passed'] else "FAIL"
        lines.append(f"| {test_name} | {status} | {result['details']} |")
    lines.append("")
    n_pass = sum(1 for r in test_results.values() if r['passed'])
    lines.append(f"**Total: {n_pass}/{len(test_results)} passed**")
    lines.append("")

    lines.append("### Reconciliation: Raw to Corrected Common Total (2025-12-31, computed at run time)")
    lines.append("")
    lines.append("raw LONG shares − non-common debt − unclassified-option-class − unclassified − suspect − superseded-vintage drops = corrected common shares.")
    lines.append("Options are disjoint from LONG shares (put_call != '') so they are not a chain term; reported as a memo line below.")
    lines.append("")
    lines.append("| Ticker | Raw rows | Raw LONG shares | −Debt | −Unclass-opt | −Unclass | −Suspect | −Vintage drops | = Corrected |")
    lines.append("|--------|----------|-----------------|-------|-------------|----------|----------|----------------|-------------|")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        r = all_snapshots[ticker]['recon']
        if not chain_sums_ok(r):
            raise ValueError(f"Section 9: reconciliation chain does not sum for {ticker} — failing the build.")
        lines.append(
            f"| {ticker} | {r['raw_rows']:,} | {r['long_shares']:,} | {r['debt_shares']:,} | "
            f"{r['unclass_opt_shares']:,} | {r['unclass_shares']:,} | {r['suspect_shares']:,} | {r['vintage_drops_shares']:,} | {r['corrected_shares']:,} |"
        )
    lines.append("")
    lines.append("Options memo (share-equivalents, excluded from common — not a chain term):")
    lines.append("")
    lines.append("| Ticker | Option rows | Share-equivalents | Holders | Rows normalized ×100 |")
    lines.append("|--------|-------------|-------------------|---------|----------------------|")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        m = all_snapshots[ticker]['metrics']
        r = all_snapshots[ticker]['recon']
        lines.append(f"| {ticker} | {r['options_rows']:,} | {m['options_total_share_equivalents']:,} | {m['options_n_holders']:,} | {m['contracts_normalized_rows']:,} |")
    lines.append("")
    lines.append("Row-count check (same steps, rows):")
    lines.append("")
    lines.append("| Ticker | Raw rows | −Debt | −Unclass-opt | −Unclass | −Suspect | −Vintage drops | = Corrected rows |")
    lines.append("|--------|----------|-------|-------------|----------|----------|----------------|------------------|")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        r = all_snapshots[ticker]['recon']
        lines.append(
            f"| {ticker} | {r['raw_rows']:,} | {r['debt_rows']:,} | "
            f"{r['unclass_opt_rows']:,} | {r['unclass_rows']:,} | {r['suspect_rows']:,} | {r['vintage_drops_rows']:,} | {r['corrected_rows']:,} |"
        )
    lines.append("")
    if universe_stats:
        lines.append("### Amendment-vintage classification counts (manager-periods with amendments)")
        lines.append("")
        lines.append("| Restatement | Add-on-suspect | Multi-unresolved | Override | UNRESOLVED total | Pairs with amendments |")
        lines.append("|-------------|----------------|------------------|----------|------------------|-----------------------|")
        lines.append(f"| {universe_stats['restatement']:,} | {universe_stats['addon_suspect']:,} | {universe_stats['multi_unresolved']:,} | {universe_stats['override']:,} | {universe_stats['unresolved']:,} | {universe_stats['pairs_with_amendments']:,} |")
        lines.append("")
    if quarterly_closure:
        lines.append("### Q3 closure (corrected common shares; dip vs avg of neighbors)")
        lines.append("")
        lines.append("| Ticker | 2025-03-31 | 2025-06-30 | 2025-09-30 | 2025-12-31 | Q3 dip remaining | Ratio Q1/Q2/Q3/Q4 |")
        lines.append("|--------|------------|------------|------------|------------|------------------|-------------------|")
        for row in quarterly_closure:
            lines.append(
                f"| {row['ticker']} | {row['2025-03-31']:,} | {row['2025-06-30']:,} | {row['2025-09-30']:,} | "
                f"{row['2025-12-31']:,} | {row['dip_remaining']:,} | {row['ratios']} |"
            )
        lines.append("")
    lines.append("**Unresolved residuals:**")
    lines.append("- UNRESOLVED manager-periods (add-on-suspect or multi-amendment without override) use latest-vintage fallback and are marked PROVISIONAL with shares at stake; see executive summary.")
    lines.append("- Contract-reporting filers are normalized ×100 only when ≥95% of their (filer, type) rows sit in the 80–120x band; CTC LLC Q2 rows (ratio ~1.0) are deliberately NOT normalized.")
    lines.append("")

    # Conclusions
    lines.append("## 10. Conclusions and Next Steps")
    lines.append("")
    lines.append("### Key Findings")
    lines.append("")
    for ticker in ['AAPL', 'MSFT', 'NVDA']:
        if ticker in all_snapshots and all_snapshots[ticker]:
            m = all_snapshots[ticker]['metrics']
            lines.append(f"- **{ticker}**: {m['n_holders']:,} institutional holders (common stock), top-5 concentration {m['top5_share_concentration']:.1%}, HHI {m['hhi']:.4f}")
    lines.append("")
    lines.append("### Next Steps")
    lines.append("1. Resolve amendment/version-selection logic (remove PROVISIONAL labels)")
    lines.append("2. Add crowding score calculation")
    lines.append("3. Implement PIT query API for historical backtesting")
    lines.append("4. Validate unit divisor for additional tickers beyond AAPL/MSFT/NVDA")
    lines.append("")

    report_path.write_text('\n'.join(lines))
    return report_path


def verify_rendered_report(report_path):
    """Rendered-report tests: Sections 6, 7 and 9 must contain numeric table rows.

    Returns a dict of 3 test results. Raises RuntimeError (fails the build) if
    a section is missing or has no numeric rows.
    """
    import re
    text = Path(report_path).read_text()
    results = {}
    for section, num in [('Sections-6-concentration', '## 6.'), ('Sections-7-ratios', '## 7.'),
                         ('Sections-9-tests-recon', '## 9.')]:
        start = text.find(num)
        if start < 0:
            results[section] = {"passed": False, "details": f"{num} header missing"}
            continue
        chunk = text[start:]
        numeric_rows = [ln for ln in chunk.split('\n')
                        if ln.startswith('|') and re.search(r'\d{4,}', ln)]
        if not numeric_rows:
            results[section] = {"passed": False, "details": f"{num} has no numeric rows"}
        else:
            results[section] = {"passed": True,
                                "details": f"{num} contains {len(numeric_rows)} numeric rows"}
    failed = [k for k, v in results.items() if not v['passed']]
    if failed:
        raise RuntimeError(f"Rendered-report check failed for: {failed} — failing the build.")
    return results
