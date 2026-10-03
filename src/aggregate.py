"""13F position aggregation: filing-vintage selection + SUM over discretion/share_class."""
import pandas as pd
import re


def clean_manager_name(name):
    """Clean stray characters from manager name."""
    if name is None:
        return 'UNKNOWN'
    name = name.strip()
    name = name.rstrip('\\')
    name = name.strip()
    return name


def get_canonical_name(df):
    """Get canonical display name per CIK: most recent filing, cleaned."""
    df = df.copy()
    df['manager_name_clean'] = df['manager_name'].apply(clean_manager_name)
    df['filed_date_str'] = df['filed_date'].astype(str)
    idx = df.groupby('manager_cik')['filed_date_str'].idxmax()
    canonical = df.loc[idx, ['manager_cik', 'manager_name_clean']].set_index('manager_cik')
    return canonical['manager_name_clean'].to_dict()


def apply_vintage_filter(df, winners):
    """Boolean mask of rows sitting in their manager's winning vintage.

    Pure set-membership on (manager_cik, filed_date, is_amendment, source) —
    therefore invariant to input row order by construction.
    """
    if df.empty:
        return pd.Series(False, index=df.index)
    rowkey = (df['filed_date'].astype(str) + '|' + df['is_amendment'].astype(str)
              + '|' + df['source'].astype(str))
    want = df['manager_cik'].astype(str).map(winners)
    return rowkey == want


def aggregate_positions(df):
    """Aggregate to (manager_cik, report_period, cusip, put_call) grain."""
    grouped = df.groupby(['manager_cik', 'report_period', 'cusip', 'put_call']).agg(
        shares=('shares', 'sum'),
        value_dollars=('value_dollars', 'sum'),
        manager_name=('manager_name', 'first'),
        filed_date=('filed_date', 'max'),
        is_amendment=('is_amendment', 'max'),
        n_discretion_buckets=('discretion', 'nunique'),
        n_share_classes=('share_class_norm', 'nunique'),
    ).reset_index()
    return grouped


def apply_value_scale_rule(df, stock_price):
    """Per (manager_cik) value-scale correction on COMMON-bucket rows.

    If at least 95% of a filer's assessable COMMON rows have value/shares within
    0.0008-0.0012 of the stock price, multiply value_dollars by 1000 for that
    filer's COMMON rows and flag them. Shares are never changed.
    Returns (df, scaled_detail_df).
    """
    df = df.copy()
    df['value_scale_corrected'] = False
    if stock_price is None or df.empty:
        return df, df.iloc[0:0]
    mask = (df['bucket'] == 'COMMON') & df['shares'].notna() & (df['shares'] > 0) & df['value_dollars'].notna()
    ratios = (df.loc[mask, 'value_dollars'] / df.loc[mask, 'shares']) / stock_price
    in_band = ratios.between(0.0008, 0.0012)
    frac = in_band.groupby(df.loc[mask, 'manager_cik']).mean()
    qualifying = set(frac[frac >= 0.95].index)
    qmask = df['manager_cik'].isin(qualifying) & (df['bucket'] == 'COMMON')
    df.loc[qmask, 'value_dollars'] = df.loc[qmask, 'value_dollars'] * 1000
    df.loc[qmask, 'value_scale_corrected'] = True
    return df, df[qmask].copy()


def apply_contracts_rule(df, stock_price):
    """Per (manager_cik, put_call) contracts normalization on OPTION rows.

    If at least 95% of a filer's rows in a (cik, put_call) group have
    (value/shares)/price in [80, 120], multiply shares by 100 and flag them.
    Keyed on put_call and filer, never on share_class.
    Returns (df, contracts_detail_df).
    """
    df = df.copy()
    df['contracts_normalized'] = False
    if stock_price is None or df.empty:
        return df, df.iloc[0:0]
    mask = (df['bucket'] == 'OPTION') & df['shares'].notna() & (df['shares'] > 0) & df['value_dollars'].notna()
    ratios = (df.loc[mask, 'value_dollars'] / df.loc[mask, 'shares']) / stock_price
    in_band = ratios.between(80, 120)
    grp = df.loc[mask, ['manager_cik', 'put_call_norm']]
    frac = in_band.groupby([grp['manager_cik'], grp['put_call_norm']]).mean()
    qualifying = set(frac[frac >= 0.95].index)
    qtuples = set(qualifying)
    qmask = df.apply(lambda r: (r['manager_cik'], r['put_call_norm']) in qtuples, axis=1) & (df['bucket'] == 'OPTION')
    df.loc[qmask, 'shares'] = df.loc[qmask, 'shares'] * 100
    df.loc[qmask, 'contracts_normalized'] = True
    return df, df[qmask].copy()


def flag_suspect_rows(df, stock_price):
    """Flag COMMON-bucket rows still below 0.5x the price (or unpriceable).

    Returns df with 'suspect' boolean column. Requires a stock price; without
    one nothing is flagged as suspect (flagged separately as price_missing).
    """
    df = df.copy()
    df['suspect'] = False
    if stock_price is None or df.empty:
        return df
    assessable = (
        (df['bucket'] == 'COMMON')
        & df['shares'].notna() & (df['shares'] > 0)
        & df['value_dollars'].notna()
    )
    vps = df.loc[assessable, 'value_dollars'] / df.loc[assessable, 'shares']
    df.loc[assessable, 'suspect'] = (vps / stock_price) < 0.5
    unpriceable = (df['bucket'] == 'COMMON') & (
        df['shares'].isna() | (df['shares'] <= 0) | df['value_dollars'].isna()
    )
    df.loc[unpriceable, 'suspect'] = True
    return df


def build_snapshot(conn, ticker, report_period):
    """Build ownership snapshot for a ticker at a report period.

    Returns a dict with aggregated frames per bucket, a runtime reconciliation
    (shares and row counts), flags, and row-level audit frames.
    Buckets: COMMON, OPTION, NON_COMMON_DEBT, UNCLASSIFIED_OPTION_CLASS,
    UNCLASSIFIED, SUSPECT. Only COMMON (non-suspect) enters common totals.
    """
    query = """
    SELECT manager_cik, manager_name, report_period, filed_date, cusip, ticker_if_known,
           value_usd, shares, share_class, put_call, discretion, is_amendment, source
    FROM main.institutional_holdings
    WHERE ticker_if_known = ? AND report_period = ?
    """
    df = conn.execute(query, [ticker, report_period]).df()
    empty_out = {
        'ticker': ticker, 'report_period': report_period,
        'common_agg': df, 'options_agg': df, 'debt_agg': df,
        'unclass_opt_agg': df, 'unclass_agg': df, 'suspect_agg': df,
        'canonical_names': {}, 'recon': {}, 'flags': {'price_missing': True},
        'audit_scaled': df, 'audit_contracts': df, 'audit_suspect': df,
    }
    if df.empty:
        return empty_out

    df['put_call_norm'] = df['put_call'].apply(lambda x: normalize_put_call(x))
    df['share_class_norm'] = df['share_class'].apply(lambda x: normalize_share_class(x))
    df['bucket'] = df.apply(lambda r: classify_bucket(r['share_class'], r['put_call_norm']), axis=1)
    df['discretion_norm'] = df['discretion'].apply(lambda x: normalize_discretion(x))
    df['value_dollars'] = df.apply(lambda r: r['value_usd'] / get_unit_divisor(r['source']), axis=1)

    canonical_names = get_canonical_name(df)

    stock_price, price_asof, price_table = get_stock_price(conn, ticker, report_period)
    flags = {
        'stock_price': stock_price,
        'price_asof': price_asof,
        'price_table': price_table,
        'price_missing': stock_price is None,
    }

    df, scaled_detail = apply_value_scale_rule(df, stock_price)
    df = flag_suspect_rows(df, stock_price)
    df, contracts_detail = apply_contracts_rule(df, stock_price)

    def step(frame, col='shares'):
        s = frame[col]
        return int(len(frame)), int(s.fillna(0).sum())

    from vintages import get_decision, UNRESOLVED_STATUSES, ensure_loaded

    ensure_loaded(conn, [str(report_period)])

    winners = {}
    unresolved = {}
    for cik in df['manager_cik'].unique():
        d = get_decision(str(cik), str(report_period))
        if d is None:
            continue
        w = d['winner']
        winners[str(cik)] = f"{w['filed_date']}|{int(w['is_amendment'])}|{w['source']}"
        if d['status'] in UNRESOLVED_STATUSES:
            stake = df[df['manager_cik'].astype(str) == str(cik)]
            stake = stake[~apply_vintage_filter(stake, winners)]
            unresolved[str(cik)] = {
                'status': d['status'],
                'stake_shares': int(stake['shares'].fillna(0).sum()),
                'stake_rows': int(len(stake)),
            }

    df['in_winning_vintage'] = apply_vintage_filter(df, winners)

    raw_long = df[df['put_call_norm'] == 'LONG']
    raw_rows, raw_shares = step(df)
    long_rows, long_shares = step(raw_long)
    debt = df[df['bucket'] == 'NON_COMMON_DEBT']
    unclass_opt = df[df['bucket'] == 'UNCLASSIFIED_OPTION_CLASS']
    unclass = df[df['bucket'] == 'UNCLASSIFIED']
    options = df[df['bucket'] == 'OPTION']
    suspect = df[(df['bucket'] == 'COMMON') & (df['suspect'])]
    common_candidates = df[(df['bucket'] == 'COMMON') & (~df['suspect'])]

    debt_rows, debt_shares = step(debt)
    uo_rows, uo_shares = step(unclass_opt)
    u_rows, u_shares = step(unclass)
    opt_rows, opt_shares = step(options)
    sus_rows, sus_shares = step(suspect)
    cand_rows, cand_shares = step(common_candidates)

    corrected = common_candidates[common_candidates['in_winning_vintage']].copy()
    # Deterministic output order (documented tie-break): no row is preferred over
    # another within a vintage; the sort only fixes display/CSV order.
    corrected = corrected.sort_values(
        ['filed_date', 'is_amendment', 'shares', 'value_dollars'], kind='stable').reset_index(drop=True)
    corr_rows, corr_shares = step(corrected)
    vdrops_rows = cand_rows - corr_rows
    vdrops_shares = cand_shares - corr_shares

    recon = {
        'raw_rows': raw_rows, 'raw_shares': raw_shares,
        'long_rows': long_rows, 'long_shares': long_shares,
        'options_rows': opt_rows, 'options_shares': opt_shares,
        'debt_rows': debt_rows, 'debt_shares': debt_shares,
        'unclass_opt_rows': uo_rows, 'unclass_opt_shares': uo_shares,
        'unclass_rows': u_rows, 'unclass_shares': u_shares,
        'suspect_rows': sus_rows, 'suspect_shares': sus_shares,
        'vintage_drops_rows': vdrops_rows, 'vintage_drops_shares': vdrops_shares,
        'corrected_rows': corr_rows, 'corrected_shares': corr_shares,
    }
    assert long_shares - debt_shares - uo_shares - u_shares - sus_shares - vdrops_shares == corr_shares, (
        f"reconciliation failed for {ticker} {report_period}: "
        f"{long_shares} - {debt_shares} - {uo_shares} - {u_shares} - {sus_shares} - {vdrops_shares} "
        f"!= {corr_shares}"
    )
    assert long_rows - debt_rows - uo_rows - u_rows - sus_rows - vdrops_rows == corr_rows, (
        f"row-count reconciliation failed for {ticker} {report_period}"
    )

    def _versioned(frame):
        if frame.empty:
            return frame
        return frame[frame['in_winning_vintage']].copy()

    common_agg = aggregate_positions(corrected)
    options_v = _versioned(options)
    options_agg = aggregate_positions(options_v) if not options_v.empty else pd.DataFrame()
    if not options_agg.empty and 'value_dollars' in options_agg.columns:
        options_agg = options_agg.drop(columns=['value_dollars'])
    debt_v = _versioned(debt)
    debt_agg = aggregate_positions(debt_v) if not debt_v.empty else pd.DataFrame()
    unclass_opt_v = _versioned(unclass_opt)
    unclass_opt_agg = aggregate_positions(unclass_opt_v) if not unclass_opt_v.empty else pd.DataFrame()
    unclass_v = _versioned(unclass)
    unclass_agg = aggregate_positions(unclass_v) if not unclass_v.empty else pd.DataFrame()
    suspect_v = _versioned(suspect)
    suspect_agg = aggregate_positions(suspect_v) if not suspect_v.empty else pd.DataFrame()

    flags['unresolved'] = unresolved
    flags['scaled_filers'] = sorted(scaled_detail['manager_cik'].unique().tolist()) if not scaled_detail.empty else []
    flags['scaled_rows'] = int(len(scaled_detail))
    flags['contracts_groups'] = (
        contracts_detail.groupby(['manager_cik', 'put_call_norm']).size().to_dict()
        if not contracts_detail.empty else {}
    )
    flags['contracts_rows'] = int(len(contracts_detail))

    return {
        'ticker': ticker, 'report_period': report_period,
        'common_agg': common_agg, 'options_agg': options_agg, 'debt_agg': debt_agg,
        'unclass_opt_agg': unclass_opt_agg, 'unclass_agg': unclass_agg, 'suspect_agg': suspect_agg,
        'canonical_names': canonical_names, 'recon': recon, 'flags': flags,
        'audit_scaled': scaled_detail, 'audit_contracts': contracts_detail,
        'audit_suspect': suspect,
    }


from normalize import (normalize_put_call, normalize_share_class, normalize_discretion,
                        get_unit_divisor, classify_bucket, get_stock_price)
