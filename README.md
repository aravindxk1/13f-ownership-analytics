# 13F Institutional Ownership Analytics

Contact: aravindxk1@protonmail.com

What this is: a read-only analytics pipeline over SEC Form 13F institutional
holdings data. It builds common-stock ownership snapshots (top holders,
concentration, HHI), quarterly changes, and shares-outstanding ratios, with a
runtime reconciliation chain that fails the build if its steps do not sum.

## Scope

- Tickers: AAPL, MSFT, NVDA only.
- Source data: 13F holdings from the SEC (via a canonical DuckDB file you
  provide; the database is opened read-only and never modified).
- Periods covered in this release: 2024-12-31 through 2025-12-31.

## How to run

```bash
pip install -e .[dev]
export HOLDINGS13F_DB="/path/to/canonical.duckdb"
export SEC_USER_AGENT="Your Name you@example.com"
python -m src.cli        # full pipeline + report to output/
pytest                   # test suite (DB-backed tests skip if HOLDINGS13F_DB is unset)
```

`HOLDINGS13F_DB` must point at the DuckDB file. `SEC_USER_AGENT` identifies any
ad-hoc SEC EDGAR fetches (descriptive name plus contact email, max 5 req/sec).

## Defect catalogue (all observed in the data, all handled or flagged)

1. Bond principal counted as shares (e.g. Barclays MSFT 2025-06-30: 2,382,351,000
   `BOND` shares) → non-common-debt bucket, excluded from common totals.
2. Option contracts reported as shares (e.g. CTC LLC, BNP Paribas at ~100x the
   stock price) → per-(filer, type) 95% rule, shares ×100, flagged.
3. Thousands-scale values (e.g. T. Rowe `COMM STK` at ~0.001x price) →
   per-filer 95% rule, value ×1000, flagged; shares never changed.
4. Amended filings double-counted (original + amendment rows coexist, e.g.
   JPMorgan AAPL Q3 raw 499,031,062 = 2x the 249,515,531 filing) → filing-vintage
   selection keeps one whole vintage; never one row per grain.
5. A confidential-treatment stub filing misread as an exit (Norges Bank Q3 2025:
   one 0-share row; ~190M AAPL / ~102M MSFT / ~326M NVDA shares absent) →
   confirmed via the `isConfidentialOmitted=true` cover page, not selling.
6. Filer errors such as value equal to shares (J. Stern NVDA Q4 reports
   value=shares=125,760,307 at $1.00/share vs $186.27 close; true position
   ≈674,318 shares) → suspect bucket, excluded, listed separately.

## SEC verification (AAPL, SH-common shares)

Every figure below was re-fetched from the linked filing and independently
re-summed from saved row-level CSVs.

| Manager | Period | Accession | SEC shares | Pipeline shares | Verdict | Filing |
|---|---|---|---|---|---|---|
| Vanguard Group | 2025-09-30 | 0000102909-25-000353 | 1,399,427,162 | 1,399,427,162 | MATCH | https://www.sec.gov/Archives/edgar/data/102909/000010290925000353/13F_0000102909_20250930.xml |
| Vanguard Group | 2025-12-31 | 0000102909-26-000031 | 1,426,283,914 | 1,426,283,914 | MATCH | https://www.sec.gov/Archives/edgar/data/102909/000010290926000031/13F_0000102909_20251231.xml |
| BlackRock, Inc. | 2025-09-30 | 0002012383-25-002949 | 1,146,332,274 | 1,146,332,274 | MATCH | https://www.sec.gov/Archives/edgar/data/2012383/000201238325002949/form13fInfoTable.xml |
| BlackRock, Inc. | 2025-12-31 | 0002012383-26-000920 | 1,154,665,731 | 1,154,665,731 | MATCH | https://www.sec.gov/Archives/edgar/data/2012383/000201238326000920/form13fInfoTable.xml |
| Berkshire Hathaway Inc | 2025-09-30 | 0001193125-25-282901 | 238,212,764 | 238,212,764 | MATCH | https://www.sec.gov/Archives/edgar/data/1067983/000119312525282901/46994.xml |
| Berkshire Hathaway Inc | 2025-12-31 | 0001193125-26-054580 | 227,917,808 | 227,917,808 | MATCH | https://www.sec.gov/Archives/edgar/data/1067983/000119312526054580/50240.xml |
| JPMorgan Chase & Co | 2025-09-30 | 0000019617-25-001081 + 0000019617-25-001093 | 236,655,531 | 236,655,531 | MATCH | https://www.sec.gov/Archives/edgar/data/19617/000001961725001081/Information_Table_09.30.2025.xml |
| JPMorgan Chase & Co | 2025-12-31 | 0000019617-26-000083 | 225,419,111 | 225,419,111 | MATCH | https://www.sec.gov/Archives/edgar/data/19617/000001961726000083/Information_Table_12.31.2025.xml |
| Morgan Stanley | 2025-09-30 | 0000895421-25-000593 + 0000895421-26-000186 | 229,103,384 | 229,103,384 | MATCH | https://www.sec.gov/Archives/edgar/data/895421/000089542125000593/US_13F-408-20250930.xml |
| Morgan Stanley | 2025-12-31 | 0000895421-26-000080 + 0000895421-26-000187 | 230,483,035 | 230,483,035 | MATCH | https://www.sec.gov/Archives/edgar/data/895421/000089542126000080/US_13F-408-20251231.xml |
| Bank of America Corp | 2025-09-30 | 0000070858-25-000440 + 0000070858-26-000025 | 123,024,725 | 123,024,725 | MATCH | https://www.sec.gov/Archives/edgar/data/70858/000007085825000440/Q3202513fhr.xml |
| Bank of America Corp | 2025-12-31 | 0000070858-26-000116 | 123,611,784 | 123,611,784 | MATCH | https://www.sec.gov/Archives/edgar/data/70858/000007085826000116/Q4202513fhr.xml |
| Goldman Sachs Group Inc | 2025-09-30 | 0000886982-25-001521 | 96,584,304 | 96,584,304 | MATCH | https://www.sec.gov/Archives/edgar/data/886982/000088698225001521/Submissioninfotable.xml |
| Goldman Sachs Group Inc | 2025-12-31 | 0000886982-26-000052 + 0000886982-26-000093 | 99,164,706 | 99,164,706 | MATCH | https://www.sec.gov/Archives/edgar/data/886982/000088698226000093/SubmissionFile.xml |
| Barclays PLC | 2025-09-30 | 0000312069-25-000619 + 0000312069-26-000090 | 41,840,175 | 41,840,175 | MATCH | https://www.sec.gov/Archives/edgar/data/312069/000031206926000090/13fInfoTable_09302025.xml |
| J. Stern & Co. LLP (NVDA) | 2025-12-31 | 0002011335-26-000001 + 0002011335-26-000002 | 125,760,307 | 0 | DIFF | stated openly: the filing reports value=shares ($1.00/share); shares excluded as suspect, true position ≈674,318 |

All six sampled amendments were full-snapshot restatements (including one labeled
NEW HOLDINGS whose content is identical); one Barclays amendment was a misfiled
Q3-2024 snapshot disowned by the filer and superseded via tests/data/edgar_overrides.csv.

## Headline results (2025-12-31, common stock)

- AAPL: 5,923 holders, 9,351,890,199 shares, $2,541,049,810,194, top-5 41.2%,
  HHI 0.0500. Top holder Vanguard ($387,749,544,852).
- MSFT: 6,111 holders, 5,382,537,841 shares, $2,603,030,147,957, top-5 37.3%,
  HHI 0.0408. Top holder Vanguard ($347,211,390,598).
- NVDA: 5,673 holders, 16,125,467,675 shares, $3,006,856,094,301, top-5 41.9%,
  HHI 0.0474. Top holder Vanguard ($422,736,430,797).
- Q3 residual dip vs avg of neighbors: AAPL 222,626,919; MSFT 67,881,378;
  NVDA 320,601,752 (down from 644,239,882 / 274,057,054 / 979,303,562 before
  vintage selection; remainder dominated by the Norges confidential-treatment gap).
- Amendment classification over 1,051 manager-periods with amendments: 732
  restatement, 259 add-on-suspect, 59 multi-unresolved, 1 override → 318 UNRESOLVED.

## LIMITATIONS

- Q3 totals and change tables are PROVISIONAL (amendment-sensitive).
- 318 UNRESOLVED amendment pairs out of 1,051 (add-on-suspect or multi-amendment
  without override); affected metrics carry shares-at-stake figures.
- Suspect and unclassified buckets are excluded from common totals and listed
  separately; some excluded shares are real (see J. Stern DIFF above).
- BNP option rows: the DB feed misses ~142 rows (SH impact 20,262 on NVDA).
- Nothing tested beyond AAPL, MSFT, NVDA.
- SEC checks were agent-assisted and independently re-summed (19/19 and 63-file
  verifier rounds, all agreeing).

Not investment advice.
