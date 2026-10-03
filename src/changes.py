"""13F quarterly ownership changes: QoQ comparisons, exits, new positions."""
import pandas as pd
import numpy as np


def compare_quarters(conn, ticker, from_period, to_period):
    """Compare institutional positions across two quarters. PROVISIONAL: amendment handling not yet resolved."""
    from aggregate import build_snapshot

    from_result = build_snapshot(conn, ticker, from_period)
    to_result = build_snapshot(conn, ticker, to_period)
    from_common, from_canonical = from_result['common_agg'], from_result['canonical_names']
    to_common, to_canonical = to_result['common_agg'], to_result['canonical_names']

    if from_common.empty or to_common.empty:
        return pd.DataFrame()

    canonical = {**from_canonical, **to_canonical}

    merged = pd.merge(
        from_common[['manager_cik', 'cusip', 'put_call', 'shares', 'value_dollars']],
        to_common[['manager_cik', 'cusip', 'put_call', 'shares', 'value_dollars']],
        on=['manager_cik', 'cusip', 'put_call'],
        how='outer',
        suffixes=('_from', '_to')
    )

    merged['shares_from'] = merged['shares_from'].fillna(0)
    merged['shares_to'] = merged['shares_to'].fillna(0)
    merged['value_dollars_from'] = merged['value_dollars_from'].fillna(0)
    merged['value_dollars_to'] = merged['value_dollars_to'].fillna(0)

    merged['shares_change'] = merged['shares_to'] - merged['shares_from']
    merged['value_change_dollars'] = merged['value_dollars_to'] - merged['value_dollars_from']

    def classify(row):
        if row['shares_from'] == 0 and row['shares_to'] > 0:
            return 'NEW'
        elif row['shares_from'] > 0 and row['shares_to'] == 0:
            return 'EXIT'
        elif row['shares_change'] > 0:
            return 'INCREASE'
        elif row['shares_change'] < 0:
            return 'REDUCTION'
        else:
            return 'HELD'

    merged['status'] = merged.apply(classify, axis=1)
    merged['manager_name'] = merged['manager_cik'].map(canonical)
    merged['is_provisional'] = True

    return merged


def get_largest_changes(changes_df, n=10):
    """Get largest increases and reductions."""
    increases = changes_df[changes_df['status'] == 'INCREASE'].nlargest(n, 'shares_change')
    reductions = changes_df[changes_df['status'] == 'REDUCTION'].nsmallest(n, 'shares_change')
    new_positions = changes_df[changes_df['status'] == 'NEW'].nlargest(n, 'shares_to')
    exits = changes_df[changes_df['status'] == 'EXIT'].nlargest(n, 'shares_from')
    return {
        'largest_increases': increases,
        'largest_reductions': reductions,
        'new_positions': new_positions,
        'exits': exits
    }


def concentration_trend(conn, ticker, periods):
    """Calculate concentration metrics across multiple periods."""
    from snapshot import build_ownership_snapshot
    results = []
    for period in periods:
        snap = build_ownership_snapshot(conn, ticker, period)
        if snap['metrics']:
            results.append({
                'report_period': period,
                'n_holders': snap['metrics']['n_holders'],
                'total_shares': snap['metrics']['total_shares'],
                'total_value_dollars': snap['metrics']['total_value_dollars'],
                'top5_concentration': snap['metrics']['top5_share_concentration'],
                'top10_concentration': snap['metrics']['top10_share_concentration'],
                'hhi': snap['metrics']['hhi'],
            })
    return pd.DataFrame(results)
