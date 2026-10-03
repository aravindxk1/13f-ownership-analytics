# Changelog

## v0.1 (2026-10-03)

First release of the 13F institutional ownership analytics pipeline.

- Common-stock ownership snapshots with top holders, concentration and HHI
  for AAPL, MSFT and NVDA.
- Filing-vintage selection: keep all rows of one winning filing vintage per
  (manager, period); restatement detection via 90% CUSIP overlap; override
  table for EDGAR-verified exceptions.
- Fail-closed share-class whitelist; non-common debt, unclassified and
  suspect buckets reported separately.
- Value-scale (thousands) correction and option-contract normalization,
  both filer-convention gated at 95%.
- Runtime reconciliation chain with build-failing assertions.
- SEC-verified regression fixtures (tests/data/edgar_expected.csv).
