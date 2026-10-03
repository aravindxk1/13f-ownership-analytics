"""13F ownership snapshots: top holders, concentration, HHI."""
import pandas as pd
import numpy as np


def calculate_hhi(shares):
    """Calculate Herfindahl-Hirschman Index from share array."""
    total = shares.sum()
    if total == 0:
        return 0.0
    weights = shares / total
    return float((weights ** 2).sum())


def calculate_concentration(shares, top_n):
    """Calculate top-N concentration (sum of top N / total)."""
    total = shares.sum()
    if total == 0:
        return 0.0
    sorted_shares = shares.sort_values(ascending=False)
    top_sum = sorted_shares.head(top_n).sum()
    return float(top_sum / total)


def build_ownership_snapshot(conn, ticker, report_period):
    """Build complete ownership snapshot with metrics.

    Options are reported as share-equivalents only (no dollar values: filers
    mix notional and premium conventions, so option dollars are not comparable).
    """
    from aggregate import build_snapshot
    result = build_snapshot(conn, ticker, report_period)
    common_df = result['common_agg']
    options_df = result['options_agg']
    canonical_names = result['canonical_names']
    recon = result['recon']
    flags = result['flags']

    base = {
        'ticker': ticker,
        'report_period': report_period,
        'snapshot': common_df,
        'top10': pd.DataFrame(),
        'options_top10': pd.DataFrame(),
        'debt_top10': pd.DataFrame(),
        'suspect_top10': pd.DataFrame(),
        'metrics': {},
        'canonical_names': canonical_names,
        'recon': recon,
        'flags': flags,
        'audit_scaled': result['audit_scaled'],
        'audit_contracts': result['audit_contracts'],
        'audit_suspect': result['audit_suspect'],
    }
    if common_df.empty:
        return base

    common_df = common_df.copy()
    common_df['manager_name'] = common_df['manager_cik'].map(canonical_names)
    common_df = common_df.sort_values('value_dollars', ascending=False).reset_index(drop=True)

    top10 = common_df.head(10).copy()
    top10['value_dollars'] = top10['value_dollars'].round(0)
    top10['shares'] = top10['shares'].round(0)

    shares = common_df['shares']
    total_shares = shares.sum()
    total_value = common_df['value_dollars'].sum()

    def _top_by_shares(frame):
        frame = frame.copy()
        frame['manager_name'] = frame['manager_cik'].map(canonical_names)
        frame = frame.sort_values('shares', ascending=False).reset_index(drop=True)
        top = frame.head(10).copy()
        top['shares'] = top['shares'].round(0)
        return top

    options_top10 = _top_by_shares(options_df) if not options_df.empty else pd.DataFrame()
    debt_agg = result['debt_agg']
    debt_top10 = _top_by_shares(debt_agg) if not debt_agg.empty else pd.DataFrame()
    suspect_agg = result['suspect_agg']
    suspect_top10 = _top_by_shares(suspect_agg) if not suspect_agg.empty else pd.DataFrame()

    metrics = {
        'n_holders': len(common_df),
        'total_shares': int(total_shares),
        'total_value_dollars': float(total_value),
        'top5_share_concentration': calculate_concentration(shares, 5),
        'top10_share_concentration': calculate_concentration(shares, 10),
        'hhi': calculate_hhi(shares),
        'top1_holder': common_df.iloc[0]['manager_name'] if len(common_df) > 0 else None,
        'top1_value_dollars': float(common_df.iloc[0]['value_dollars']) if len(common_df) > 0 else 0,
        'options_total_share_equivalents': int(options_df['shares'].sum()) if not options_df.empty else 0,
        'options_n_holders': len(options_df),
        'contracts_normalized_rows': flags.get('contracts_rows', 0),
        'debt_total_shares': int(debt_agg['shares'].sum()) if not debt_agg.empty else 0,
        'debt_n_holders': len(debt_agg),
        'suspect_total_shares': int(suspect_agg['shares'].sum()) if not suspect_agg.empty else 0,
        'suspect_n_holders': len(suspect_agg),
        'scaled_rows': flags.get('scaled_rows', 0),
        'price_missing': flags.get('price_missing', False),
        'unresolved_n_managers': len(flags.get('unresolved', {})),
        'unresolved_stake_shares': sum(
            v['stake_shares'] for v in flags.get('unresolved', {}).values()),
    }

    return {
        'ticker': ticker,
        'report_period': report_period,
        'snapshot': common_df,
        'top10': top10,
        'options_top10': options_top10,
        'debt_top10': debt_top10,
        'suspect_top10': suspect_top10,
        'metrics': metrics,
        'canonical_names': canonical_names,
        'recon': recon,
        'flags': flags,
        'audit_scaled': result['audit_scaled'],
        'audit_contracts': result['audit_contracts'],
        'audit_suspect': result['audit_suspect'],
    }
