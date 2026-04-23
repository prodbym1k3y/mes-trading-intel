---
name: regime-check
description: POSTMORTEM context snapshot — what the market structure looked like (VWAP, opening range, prior day H/L, gamma levels, VIX) at a specific time or now. Used for analyzing trades after the fact, NOT for entry signals. Mirrors `market_context.py` + `gamma_analysis.py` on PC. Trigger phrases include "context check", "what was the regime", "market context", "gamma levels", "regime for this trade".
---

# Regime Check — Postmortem Market Context

Per Jaime's explicit system philosophy: this is NOT a signal generator. It reports what market structure looked like at a point in time (usually the entry of a trade being reviewed) so Jaime can understand why a setup worked or didn't.

## Required context

Load `mes-context` first. Authoritative PC tools: `python ~/mes-intel/tools/market_context.py` and `gamma_analysis.py`. These fetch live ES bars + VIX + SPX options chain. This skill surfaces already-computed context from `journal/trade_context.csv` if available.

**Hard rule**: this skill does not output "bullish / bearish" or "go / no-go" verdicts. It describes structure. User draws conclusions.

## Inputs

- A trade reference (trade_num, date, or timestamp) OR "current"
- If "current" and user is live-trading, gently redirect: "Live trading → don't look here for an entry opinion. Use `/pre-trade-checklist` for friction instead. This skill is for postmortem."

## Workflow

### 1. Pull trade context if it exists

Check `journal/trade_context.csv` (populated by `market_context.py`) for the trade's pre-annotated context. Columns likely include:

- micro_trend_5min, micro_trend_15min, micro_trend_30min
- vwap_distance (points from VWAP at entry)
- opening_range_position (above/below/within)
- prior_day_high, prior_day_low (distance to each)
- fifteen_min_break_state (holding break, failed break, consolidating)
- mfe_next_10bars, mae_next_10bars

If context exists: render each field as a line, with a one-word interpretation (neutral words only — "above VWAP" not "bullish").

If context doesn't exist for this trade:
- "No trade_context.csv entry — run `python ~/mes-intel/tools/market_context.py --trade N` on PC to generate."

### 2. Gamma snapshot (if available)

If the trade has an associated gamma_analysis output (timestamped), surface:

```
At trade time:
  Spot: $X
  Zero-gamma level: $Y
  Closest call wall above: $Z (distance +$A)
  Closest put support below: $B (distance -$C)
  VIX: X
```

Caveat disclosure: "Gamma = SPX snapshot, approximated to ES at +20 pts fair value. Inexact but structural signal is directional."

If no gamma data: say so.

### 3. MFE/MAE analysis

From trade_context.csv:

```
Trade's actual ticks captured: N
MFE over next 10 bars from entry: M ticks  → % captured: X%
MAE over next 10 bars: P ticks  → how deep you went underwater: $Y
```

If Jaime captured <40% of MFE: "Exit may have been early." (Observation, not prescription.)
If MAE was >60% of eventual stop: "You survived a deep pullback to a winner — patience was rewarded on this one."

### 4. Structural observations (not signals)

Two or three structural observations about the setup's context:

- "Entry was <above|below|at> VWAP (distance X pts)."
- "Price was in upper/lower third of opening range."
- "Prior day high was <N pts above|below> entry."
- "15-min break had <held|failed|was in progress>."

Never connect these to "this means you should have done X." Only describe.

### 5. Reference PC tool

End with:

```
For full postmortem with per-trade insights:
  python ~/mes-intel/tools/trade_postmortem.py --trade <N>

Or a whole week's review:
  python ~/mes-intel/tools/trade_postmortem.py --week
```

## Output format

Trade identification → context fields → gamma → MFE/MAE → structural notes → PC tool ref.

20-35 lines.

## Don'ts

- **NEVER output "bullish / bearish / long bias / short bias" framing.** Describe structure, not direction.
- Don't connect context to "should have done X" — structural observations only.
- Don't treat yfinance data as authoritative — flag it as approximate.
- Don't use this skill live (during market). It's a review tool.
- Don't invent gamma or MFE data if the source file doesn't have it. Say so instead.
