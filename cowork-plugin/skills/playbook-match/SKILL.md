---
name: playbook-match
description: Find historical analogs in Jaime's trade journal for the current setup. Searches journal/all_trades.csv and apex_grade_log.csv for trades matching side + time-bucket + size + session context, returns outcome stats and proximity to the proven 10-pt hold prototype. Trigger phrases include "find similar", "have I traded this before", "playbook check", "pattern match", "historical edge", "analog".
---

# Playbook Match — Finding Your Historical Analogs

Jaime sees something forming and asks: have I been here before, and what happened?

## Required context

Load `mes-context` first. Default account: **APEX-565990-01** (but optionally combine PERSONAL for pattern recognition when explicitly asked — tag clearly in output).

## Inputs to gather

Ask (any missing):

1. **Side** — long / short
2. **Time-bucket** (ET window) — or auto-detect from current Phoenix time
3. **Setup description** — one line, what you're seeing (e.g. "VWAP reclaim", "GEX wall fade", "opening range break")
4. **Position in session** — is it trade #N of today? Already green/red?
5. **Proposed size** — 5ct / 7ct / other

## Workflow

### 1. Filter trade journal for analogs

From `journal/all_trades.csv`, filter to APEX trades matching:

- Same `direction`
- Similar `entry_time` hour bucket (±1 hour)
- Similar `contracts` size
- Similar `session_trade_num` (within ±2)

For each analog, extract: date, ticks, duration, pnl_dollars, cum_pnl at time of trade.

### 2. Stats summary

```
Analogs found: N
Same side + time bucket: M
Win rate: X%
Avg pnl_dollars: $Y
Avg hold: Z min
Best: $A on <date>
Worst: -$B on <date>
Profit factor: <pf>
```

If N < 5: "Low sample — these stats are directional only."

### 3. Proximity to the 10-pt hold prototype

Compare the current setup against the Thu+Fri 4/16-4/17 prototype:

| Prototype condition | Current setup | Match |
|---|---|---|
| Not first trade of session | trade #N | ✓/✗ |
| 9:41-11:00 ET window | <current_time> ET | ✓/✗ |
| Fewer total trades that day (3-4 target) | <today_count> so far | ✓/✗ |
| Already green in session | <session_state> | ✓/✗ |

```
PROTOTYPE SIMILARITY: N/4 conditions match
```

If 4/4: "This is textbook. The data says patient hold works from here."
If 3/4: "Close to prototype. Name what's missing before entering."
If ≤2/4: "Not the proven window. Scalp pace at best, no runner thesis."

### 4. Apex runner log cross-check

Read `journal/apex_grade_log.csv`. Filter to runners matching current setup (same time_bucket, direction). Render:

```
Runners from this exact slice (time_bucket × direction):
  - 2026-04-16 09:41 long 5ct (held 8m07s): net +$243, tape strong_trend
  - 2026-04-17 10:51 short 7ct (held 8m36s): net +$174, tape strong_trend
  
Common features among winners:
  - All held through at least one pullback
  - All had tape_trend = strong_trend
  - Confidence_1to5 was 4+ 
```

If only 1-2 runners match: "Thin sample — Jaime's runner book is young (first 2 logged 4/16-17)."

### 5. Edge / leak tagging

Cross-reference the current setup against `claude-memory/project_current_edges.md` and `project_current_leaks.md`:

```
Matches edges:  <list of slices it belongs to>
Matches leaks:  <list of slices it belongs to>
Net expectancy from those slices: $±X
```

### 6. Verdict

```
Edge strength:   <strong | moderate | weak | negative>
Best version:    <specific conditions where historical win rate is highest>
Avoid if:        <conditions that historically broke the analog>
```

## Output format

Stats summary → prototype match → runner analogs → edge/leak tags → verdict.

30-50 lines.

## Don'ts

- Don't claim "strong edge" with N < 10 analogs. "Directional" is the right word under that.
- Don't mix PERSONAL and APEX analogs unless explicitly asked — tag accounts separately.
- Don't suggest position sizing — account state is `/prop-firm-status`'s job.
- Don't confuse "many winners at 9:41-11:00" with "this current 9:45 setup will win." Analogs are probability, not prediction.
