# PR 1b Draft — Historical USD-M funding settlements + versioned maintenance brackets

## Stack note

Second slice of the Binance USD-M data-contract stack (`#462`). Cut fresh from
`upstream/main` as `pr/usdm-funding-brackets` — **not** rebased from
`codex/binance-usdm-data`, since that branch's slice 1 commits already merged
in a different shape as [#470](https://github.com/HKUDS/Vibe-Trading/pull/470).
Only the funding-settlement idea from that branch's third commit (`6d77306`)
carries forward here as reference; the code is re-applied against the
post-#470 file, and the maintenance-bracket half of this PR is new work not
present on that branch. PR 2 (`docs/superpowers/pr-drafts/pr2-margin-risk.md`)
rebases onto this PR once it merges.

## Summary

- Fetch historical funding-rate settlements for `*-USDT-PERP` symbols and
  align them to the required 00:00/08:00/16:00 UTC settlement bars.
- Fetch the current per-symbol maintenance-margin bracket schedule
  (`notional_cap`, `maintenance_rate`, `cumulative_maintenance_amount` per
  bracket) and stamp it with a content-hash version.
- Fail closed when funding settlements are missing, duplicated, or when the
  bracket schedule is missing, incomplete, or not strictly increasing by
  notional cap.

## Why

Part of `#462`. Maintainer guidance on the merged #470 thread: "Still open
for a follow-up PR (1b): historical funding-rate settlement and
maintenance-margin brackets. Same constraints apply — opt-in, fail-closed,
spot untouched." This PR is that follow-up. It does not wire funding or
brackets into `CryptoEngine`'s accounting or liquidation path — that
integration is PR 2 (the pure margin risk model). This PR only extends the
data contract established by #470.

## Changes

`agent/backtest/loaders/ccxt_loader.py`:

- `_fetch_perpetual` now also calls `_fetch_funding_history` and
  `_fetch_maintenance_brackets` for `-PERP` symbols only; spot fetches are
  untouched.
- `_fetch_funding_history`: paginated `fetch_funding_rate_history` fetch
  bounded by the existing retry/budget helpers (same pattern as the OHLCV
  fetch). Adds `funding_rate` (0.0 outside settlement bars) and
  `funding_settlement_time` (`NaT` outside settlement bars) columns.
  - Fail-closed: raises `ValueError` if any bar at hour 0/8/16 UTC has no
    matching funding settlement, or if the funding history contains a
    duplicate settlement timestamp.
- `_fetch_maintenance_brackets`: calls `fetch_leverage_tiers([symbol])` and
  reads `notionalCap` / `maintMarginRatio` / `cum` / `bracket` from each
  tier's raw `info` payload (the four required fields named in `#462`'s
  accounting-boundary section). Adds `maintenance_brackets` (JSON-encoded
  list of bracket records) and `maintenance_bracket_version` (16-hex-char
  SHA-256 of the canonical bracket JSON) columns, constant across every row
  of the returned frame.
  - Versioning rationale: CCXT/Binance expose only the *current* bracket
    table, not bracket history, so there is no exchange-native version
    number. The content hash changes whenever Binance changes the schedule,
    giving a fail-closed way to detect a stale or swapped-out bracket set
    without requiring a real historical bracket API.
  - Fail-closed: raises `ValueError` if the bracket fetch returns nothing,
    if any tier is missing a required field, or if notional caps are not
    strictly increasing across tiers.
- Both additions are encoded as ordinary DataFrame columns (not
  `DataFrame.attrs`) so they survive the existing opt-in parquet loader
  cache unchanged.

## Test Plan

- [x] `pytest agent/tests/test_ccxt_perpetual_loader.py -q` — 16 passed
  (8 pre-existing + 8 new: funding alignment, missing/duplicate funding
  settlement, bracket shape + version, version changes with bracket
  contents, missing/incomplete/non-monotonic brackets).
- [x] `pytest agent/tests/test_ccxt_perpetual_loader.py agent/tests/test_ccxt_loader_bounded.py agent/tests/test_ccxt_loader_proxy.py agent/tests/test_crypto_engine.py agent/tests/test_base_engine.py -q` — 75 passed (default spot/crypto-engine path regression, unchanged).
- [x] `ruff check agent/backtest/loaders/ccxt_loader.py agent/tests/test_ccxt_perpetual_loader.py` — all checks passed (one pre-existing `# noqa` format warning on an unrelated line, confirmed present on unmodified `upstream/main` too).
- [x] `bash tools/ci_grep_gates.sh` — all 5 gates passed (gate e: zero raw `os.getenv`/`os.environ` reads added; the one WARN is pre-existing and in `agent/src/providers/llm.py`, untouched by this PR).
- [x] `git diff --stat upstream/main` — 2 files changed, 343 insertions(+), 1 deletion(-) (within the ~500-line budget including tests).
- [ ] Full suite (`pytest agent/tests --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --continue-on-collection-errors -q`) — run locally; paste final pass/fail/error counts here and confirm no new failures beyond what reproduces on a clean `upstream/main` before opening the PR.

## Checklist

- [x] No changes to protected areas (`src/agent/`, `src/session/`, `src/providers/`)
- [x] No hardcoded values; no raw env reads (AST-gated)
- [x] Follows CONTRIBUTING.md
- [x] No market data files committed; test fixtures are synthetic/mocked
- [x] No live-endpoint imports reachable from the backtest path
- [x] Opt-in only: activates strictly on the explicit `-PERP` suffix; default spot path and `CryptoEngine` are unchanged

## Safety and network risk

- Runtime access is public, read-only CCXT market data (`fetch_funding_rate_history`,
  `fetch_leverage_tiers`); no API key is accepted or required.
- Tests use a fake exchange class with synthetic funding rows and bracket
  tiers — no network requests, no committed market data.
- No broker, OAuth, wallet, order, deployment, or credential surface changes.

## Fidelity notes

- Maintenance brackets are a **current snapshot**, not a historical series:
  CCXT/Binance do not expose bracket history, so the same bracket table (and
  its version hash) is applied uniformly across the requested date range.
  This is a known, documented limitation — a strategy backtesting far enough
  back that Binance's real bracket table differed will not see that
  difference reflected. PR 2's risk model is expected to treat the bracket
  version as an explicit fidelity input rather than assume perfect historical
  accuracy.
  - **Verify before submitting**: confirm this snapshot-only behavior is an
    acceptable interpretation of "versioned per-symbol maintenance brackets"
    per `#462`'s accounting boundary, since no historical bracket API exists
    to do better.
- Funding settlements only cover the exact `since_ms`/`end_ms` window
  requested, same bounding behavior as the existing OHLCV/mark fetch.

## Out of scope

- Wiring funding rate / maintenance brackets into `CryptoEngine` accounting
  or liquidation checks — PR 2 (pure margin risk model).
- Isolated/cross liquidation, position scaling, open orders, paper trading,
  live account reads, and live execution (PR 2/PR 3 and beyond).

## Rollback

Revert this PR's commit(s). Spot symbols and the pre-#470 execution/mark-only
perpetual path are unaffected; disabling `*-PERP` usage remains an immediate
operational rollback, same as #470.
