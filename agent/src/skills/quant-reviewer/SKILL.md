---
name: quant-reviewer
description: Judges a quantitative index forecast (e.g. the hub Ridge model) against scanner-surfaced surprises, producing a labeled second opinion with explicit disagreements and a confidence score. Never overwrites the model baseline.
category: analysis
---

# Quant Review Judge

## Purpose

Merges a quantitative model's baseline forecast (e.g. the hub's Ridge
index-return prediction) with the surprises surfaced by a scanning skill (see
`index-advisor`) into one labeled, structured second opinion. The judge's job
is to disagree explicitly when the evidence warrants it, not to reproduce or
silently override the model.

This skill exists because a single-model forecast, however well-calibrated on
history, can miss a same-day regime shift a human reviewer would catch
immediately — a flow reversal, a vol spike, or a technical picture that
flatly contradicts the model's stated direction. The judge's output makes
that disagreement visible and auditable instead of leaving it implicit.

## Inputs

- The model baseline: `prediction.view` and `expected_return_pct` from the hub
  trade plan or index research artifact — treat this as ground truth for
  *what the model said*, never as something to recompute.
- The scanner's structured output (`surprises[]`, `technical_readings`,
  `data_freshness_notes`) — typically produced by the `index-advisor` skill in
  a prior task.
- Any prior review's `active_strategy_profile` and `technical_interpretation`,
  if available, for continuity across repeated reviews of the same target.

## Rules

1. **The model baseline is immutable input, not a draft.** `prediction.view`
   and `expected_return_pct` are never edited, rounded, or "corrected" by this
   skill — they are quoted as-is.
2. **Every output carries a disclaimer**: `"Reviewer opinion — separate from
   Ridge headline forecast."` This keeps a downstream consumer from
   conflating the review with the model's own confidence.
3. **Disagreement must be explicit.** When technical consensus, flows, or
   volatility readings from the scanner contradict the model's stated
   direction, list it in `disagreements_with_forecast[]` — don't average it
   away into a vague "mixed signals" summary.
4. **Confidence is evidence-scaled, not vibes.** Assign `review_confidence` in
   the range 0.5–0.85 based on how much of the scanner's evidence agrees with
   or contradicts the model — never claim near-certainty (>0.85) from a
   same-day scan, and never go below 0.5 (a low-confidence review is still a
   review, not a coin flip).
5. **No trade execution, ever.** This skill (and its scanner input) has no
   order-placement authority. Execution — where the system has a configured
   connector — is a separate, structurally isolated path.

## Required Outputs

1. **TA consensus** — one of bull / bear / neutral, with a one-line rationale
   citing the specific reading that drove the call.
2. **Active strategy profile** — pick one from the factor playbook (e.g.
   `momentum`, `mean_reversion`, `flow_driven`) that best matches current
   conditions, plus a short note for options-desk handoff.
3. **Disagreements** — `disagreements_with_forecast[]`, each entry naming the
   specific scanner finding and how it contradicts the model view. Empty list
   is a valid, honest output when nothing contradicts the model.
4. **Surprises** — the top 3 actionable items from the scanner's `surprises[]`,
   ranked by how much they'd change a trader's read of the situation.
5. **Technical interpretation** — one paragraph, written for injection into a
   downstream agent's context, summarizing the technical picture in plain
   language (not just the raw numeric readings).

## Output Shape

```
disclaimer: "Reviewer opinion — separate from Ridge headline forecast."
ta_consensus: bull | bear | neutral
ta_rationale: one line
active_strategy_profile: momentum | mean_reversion | flow_driven | ...
disagreements_with_forecast:
  - finding vs. model direction, one line each (may be empty)
surprises_top3:
  - up to 3, ranked by materiality
review_confidence: 0.5-0.85
technical_interpretation: one paragraph
```
