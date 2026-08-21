---
name: index-advisor
description: India index (NIFTY/BANKNIFTY) research scanner — reads hub index research and live OpenAlgo context, surfaces flow/vol/technical surprises the headline forecast may underweight. Read-only, no execution.
category: analysis
---

# India Index Advisor

## Purpose

Scans the current state of an Indian benchmark index (NIFTY, BANKNIFTY) around a
forecast horizon and surfaces surprises a single quantitative model — typically
the hub's Ridge index-return forecast — may not weight correctly: a flow
reversal, a volatility-regime shift, or a technical-consensus disagreement with
the model's stated direction.

This skill is a **second-opinion input**, not a forecast. It never proposes a
trade or changes the model's prediction — it hands a judge (see the
`quant-reviewer` skill) a structured list of things worth checking before that
prediction is trusted at face value.

Applicable scenarios:
- A scheduled or on-demand quant review of a live index forecast
- Sanity-checking a model prediction before it reaches an autonomous agent's
  trade plan
- Explaining *why* a forecast might be stale (data gaps, regime change) rather
  than just stating a number

## Required Inputs

Pull whichever of these are available and freshest:

- `get_index_trade_plan(ticker=<target>, horizon_days=<horizon>)` — the current
  hub trade plan and headline `prediction.view` / `expected_return_pct`
- Hub `index_research/latest.json` when the trade-plan tool is stale or
  unavailable
- `run_quant_review(ticker=<target>, horizon_days=<horizon>)` to force a fresh
  pull when the cached research is out of date
- Factor-playbook context: `technical_interpretation` and
  `active_strategy_profile` from prior review artifacts, if present

## Scan Dimensions

1. **Flows** — FII/DII net flow over the last 5 sessions, and whether the
   absorption ratio (how much of one side's selling the other side absorbed)
   agrees or disagrees with the model's forecast direction.
2. **Volatility regime** — India VIX level and trend, realized vol vs. implied,
   put-call ratio, and derivatives skew (`qfinindia_*` tools where available).
   A forecast generated in a low-vol regime deserves less confidence once VIX
   has moved materially.
3. **Technical consensus** — RSI, MACD histogram sign/slope, and price distance
   from key moving averages, compared against the model's `view`. Agreement
   raises confidence; disagreement is the headline surprise to report.
4. **Calendar effects** — expiry week (index options unwind can distort the
   last 2-3 sessions), Union Budget day, and earnings/results season, all of
   which can produce moves the model's training data underweights.
5. **Data freshness** — flag any of the above factors that are missing or
   stale rather than silently omitting them; a gap is itself a finding.

## Output Shape

Produce structured bullets, not prose paragraphs, so the reviewing judge can
merge them mechanically:

```
surprises:
  - kind: flow | vol | technical | calendar | data_gap
    message: one-line description
    category: bullish | bearish | neutral | informational
technical_readings:
  - the specific numeric levels cited (RSI value, VIX level, MA distance, etc.)
data_freshness_notes:
  - only gaps — omit this section entirely if everything is current
```

## Hard Rules

- **Never** change or restate the model's forecast as if it were this skill's
  own output — surface disagreement, don't overwrite.
- **Never** place, size, or recommend an order. This skill has no execution
  authority; OpenAlgo (or whichever connector is configured) is the sole
  execution path elsewhere in the system.
- Report absence of data as a `data_gap` surprise rather than skipping the
  dimension silently — a reviewer working off missing flow data needs to know
  that, not just get a shorter list.

## Handoff

This skill's output is designed to feed directly into the `quant-reviewer`
skill's judge role, which merges it against the model baseline and produces
the final labeled second opinion (including `disagreements_with_forecast[]`
and a `review_confidence` score). See that skill for the merge rules.
