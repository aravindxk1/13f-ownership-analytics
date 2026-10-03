"""13F data normalization: units, put_call, share_class."""
import re

# Unit divisor keyed on source batch (verified: cutover at 2023q1_form13f.zip)
UNIT_DIVISOR_MAP = {
    "2022q4_form13f.zip": 1000,
    "2022q3_form13f.zip": 1000,
    "2022q2_form13f.zip": 1000,
    "2022q1_form13f.zip": 1000,
    "2021q4_form13f.zip": 1000,
    "2021q3_form13f.zip": 1000,
    "2021q2_form13f.zip": 1000,
    "2021q1_form13f.zip": 1000,
    "2020q4_form13f.zip": 1000,
    "2020q3_form13f.zip": 1000,
    "2020q2_form13f.zip": 1000,
    "2020q1_form13f.zip": 1000,
    "2019q4_form13f.zip": 1000,
    "2019q3_form13f.zip": 1000,
    "2019q2_form13f.zip": 1000,
    "2019q1_form13f.zip": 1000,
    "2018q4_form13f.zip": 1000,
    "2018q3_form13f.zip": 1000,
    "2018q2_form13f.zip": 1000,
    "2018q1_form13f.zip": 1000,
    "2017q4_form13f.zip": 1000,
    "2017q3_form13f.zip": 1000,
    "2017q2_form13f.zip": 1000,
    "2017q1_form13f.zip": 1000,
    "2016q4_form13f.zip": 1000,
    "2016q3_form13f.zip": 1000,
    "2016q2_form13f.zip": 1000,
    "2016q1_form13f.zip": 1000,
    "2015q4_form13f.zip": 1000,
    "2015q3_form13f.zip": 1000,
    "2015q2_form13f.zip": 1000,
    "2015q1_form13f.zip": 1000,
    "2014q4_form13f.zip": 1000,
    "2014q3_form13f.zip": 1000,
    "2014q2_form13f.zip": 1000,
    "2014q1_form13f.zip": 1000,
    "2013q4_form13f.zip": 1000,
    "2013q3_form13f.zip": 1000,
    "2013q2_form13f.zip": 1000,
    "2013q1_form13f.zip": 1000,
}

def get_unit_divisor(source_batch):
    """Return unit divisor for a source batch. Default 1 (dollars) for unknown/newer batches."""
    if source_batch is None:
        return 1
    # Check exact match first
    if source_batch in UNIT_DIVISOR_MAP:
        return UNIT_DIVISOR_MAP[source_batch]
    # Check if it's a newer batch (01jan2024+ format) -> dollars
    if re.match(r'^\d{4}[a-z]+\d{4}', source_batch):
        return 1
    # Default to dollars for unknown batches
    return 1

def normalize_put_call(put_call):
    """Normalize put_call field."""
    if put_call is None or put_call == '':
        return 'LONG'
    pc = put_call.strip().upper()
    if pc == 'CALL':
        return 'CALL'
    if pc == 'PUT':
        return 'PUT'
    return 'LONG'

COMMON_STOCK_WHITELIST = {
    'COM', 'COMMON', 'CMN', 'STOCK', 'SHS', 'COMMON STOCK', 'COMM STK', 'COMMON SHARES',
    'CL A', 'CLASS A', 'CL A NEW', 'CLASS A NEW', 'COM CL A',
    'CL B', 'CLASS B', 'CL B NEW', 'CLASS B NEW', 'COM CL B',
    'ETF', 'SPONSORED ADR', 'ADR', 'ADS', 'SPONSORED ADS',
}

DEBT_TOKENS = {'BOND', 'BONDS', 'NOTE', 'NOTES', 'DEBT', 'DEBENTURE', 'DEBENTURES', 'CONVERTIBLE'}
OPTION_TOKENS = {'OPTION', 'OPTIONS', 'OPT', 'CALL', 'PUT', 'WARRANT', 'WARRANTS'}


def _tokens(raw_upper):
    """Split a raw label into alpha tokens for keyword matching."""
    return set(re.split(r'[^A-Z]+', raw_upper)) - {''}


def normalize_share_class(share_class):
    """Normalize share_class to canonical categories. Fail closed: unknown -> UNCLASSIFIED."""
    if share_class is None or share_class == '':
        return 'UNCLASSIFIED'
    sc = share_class.strip().upper()
    if sc in COMMON_STOCK_WHITELIST:
        if sc in ('COM', 'COMMON', 'CMN', 'STOCK', 'SHS', 'COMMON STOCK', 'COMM STK', 'COMMON SHARES'):
            return 'COMMON'
        if sc.startswith('CL') or sc.startswith('CLASS'):
            if 'B' in sc:
                return 'CLASS_B'
            return 'CLASS_A'
        if sc == 'ETF':
            return 'ETF'
        return 'ADR'
    toks = _tokens(sc)
    if toks & DEBT_TOKENS:
        return 'NON_COMMON_DEBT'
    if toks & OPTION_TOKENS:
        return 'UNCLASSIFIED_OPTION_CLASS'
    return 'UNCLASSIFIED'


def classify_bucket(share_class, put_call):
    """Assign a row to a reporting bucket. put_call may be raw or normalized.

    Buckets: COMMON (whitelisted common labels, empty put_call), OPTION (any
    CALL/PUT row — keyed on put_call, never on share_class), NON_COMMON_DEBT,
    UNCLASSIFIED_OPTION_CLASS (option-style class with empty put_call),
    UNCLASSIFIED (everything else). Only COMMON enters common-stock totals.
    """
    if normalize_put_call(put_call) in ('CALL', 'PUT'):
        return 'OPTION'
    label = normalize_share_class(share_class)
    if label in ('COMMON', 'CLASS_A', 'CLASS_B', 'ETF', 'ADR'):
        return 'COMMON'
    return label


def is_common_stock(bucket_or_label):
    """Return True only for common-stock buckets/labels."""
    return bucket_or_label in ('COMMON', 'CLASS_A', 'CLASS_B', 'ETF', 'ADR')


def get_stock_price(conn, ticker, report_period):
    """Exact close on report_period from main.market_prices_daily (asset=ticker, source='yahoo').

    Returns (price, asof_date, source_table) or (None, None, source_table) if missing.
    A missing price must block ratio-based normalization (flag, do not normalize).
    """
    row = conn.execute(
        "SELECT close, date, source FROM main.market_prices_daily "
        "WHERE asset = ? AND date = ? AND source = 'yahoo'",
        [ticker, report_period],
    ).fetchone()
    if row is None or row[0] is None:
        return None, None, 'main.market_prices_daily'
    return float(row[0]), str(row[1]), 'main.market_prices_daily'


def is_options_contract(share_class_norm, put_call_norm, implied_price, stock_price):
    """Detect if an option position is reported in contracts instead of shares.
    
    Returns True if the implied price is ~100x the stock price, indicating
    the filer reported contracts (1 contract = 100 shares).
    """
    if put_call_norm not in ('CALL', 'PUT'):
        return False
    if stock_price <= 0 or implied_price <= 0:
        return False
    ratio = implied_price / stock_price
    return 80 <= ratio <= 120


def normalize_options_contracts(shares, share_class_norm, put_call_norm, implied_price, stock_price):
    """Convert option contracts to shares if detected.
    
    If the position is reported in contracts (implied price ~100x stock price),
    multiply shares by 100 to get the equivalent number of shares.
    """
    if is_options_contract(share_class_norm, put_call_norm, implied_price, stock_price):
        return shares * 100
    return shares

def normalize_discretion(discretion):
    """Normalize discretion field."""
    if discretion is None or discretion == '':
        return 'UNKNOWN'
    d = discretion.strip().upper()
    if d == 'SOLE':
        return 'SOLE'
    if d == 'DFND':
        return 'DFND'
    if d == 'OTR':
        return 'OTR'
    return f'OTHER:{discretion}'
