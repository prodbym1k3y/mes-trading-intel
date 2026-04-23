---
name: identify-leak
description: Find what's quietly draining P&L on the APEX eval. Examines all trades for systematic losing slices — time-of-day, contract size, hold duration, direction, day-of-week, opening-bell behavior. Mirrors `leak_detector.py`. Returns top 3-5 leaks ranked by $ impact with the smallest behavioral fix per leak. Trigger phrases include "find my leaks", "what's costing me", "where am I bleeding", "leak check", "leak detector".
---

# Identify Leak — Where the Money Quietly Goes

Most traders have a few systematic errors that account for the bulk of losses. This skill mines `journal/all_trades.csv` to find them.

## Required context

Always load `mes-context` first. Default account: **APEX-565990-01**. Default lookback: all APEX trades (since 2026-04-13). Authoritative version: `python ~/mes-intel/tools/leak_detector.py`.

## Workflow

### 1. Slice the trades

Read `journal/all_trades.csv`, filter `APEX-565990-01`. Group trades along these dimensions:

- **direction** (long / short)
- **hold_duration**: quick (<3m), patient (3-15m), long (>15m)
- **contract_size** (1ct, 2ct, 5ct, 7ct)
- **hour** (entry hour ET — 06h, 07h, ...)
- **day_of_week** (Mon, Tue, ..., Fri)
- **trade_number** (#1, #2, #3, ... within session)
- **session_grade** (A_or_B vs C_or_worse — join against `rule_compliance.csv`)
- **direction_time** (e.g. short_midday, long_open, etc.)
- **session_pnl_entering** ("trade #N when session was already up/down/flat")

For each slice, compute:

- N (sample count)
- NET P&L (sum of pnl_dollars)
- Win rate
- Avg pnl_dollars
- Wilson lower-bound 95% confidence on win rate (so we don't over-trust thin samples)

### 2. Filter for actual leaks

A leak is a slice where:
- NET P&L is negative
- N >= 5 (otherwise it's noise — but sample size 3 acceptable for high-impact slices like grade)

Score each leak by:
```
leak_score = abs(NET) × confidence_multiplier
```

Where confidence_multiplier rewards larger samples.

### 3. Top 3-5 leak block

For each leak (max 5):

```
LEAK <N>: <slice name> (<dimension>)
  NET: -$X over N trades · win rate Y%
  Avg per trade: -$Z
  Contributes ~Q% of total APEX losses
  
  THE BEHAVIOR: <what is actually happening>
  THE FIX: <single specific behavioral change>
  TEST: <how Jaime knows it's working in 2 weeks>
```

Examples of "THE BEHAVIOR + FIX" framing:

- **quick (<3m) leak**: "Scalping under 3 minutes. Commissions eat gross. FIX: every entry needs a 6-min minimum hold thesis written before clicking. TEST: avg hold > 5 min over 2 weeks."
- **5ct contract_size leak**: "5-contract trades net negative — a sizing-discipline issue, not size itself. FIX: 5ct only on A-grade setups (run /pre-trade-checklist). TEST: 5ct trades with verdict 'PROCEED' should net positive over 2 weeks."
- **Mon day_of_week leak**: "Monday is your worst day historically. FIX: Mon is sim-only OR max-3-trade day. TEST: Mon net P&L improves by half over next 4 Mondays."
- **C_or_worse session_grade leak**: "Bad-grade sessions cost you $574. The macro fix is the streak.py threshold — if score drops below 75 mid-session, walk. TEST: zero C-or-worse sessions for 5 sessions."
- **#2 trade_number leak**: "The 2nd trade of every session is consistently your worst. FIX: 90-sec mandatory pause after trade #1 regardless of outcome. TEST: trade #2 net P&L turns positive over 10 sessions."

### 4. The "easiest dollar"

Identify the leak with the lowest behavioral cost per dollar saved. Surface as:

```
EASIEST DOLLAR THIS WEEK: <leak name>
  Single change required: <one sentence>
  Estimated weekly recovery: $X
```

### 5. Cross-reference to current edges

Read `claude-memory/project_current_edges.md`. If a top edge slice exists that is the inverse of a top leak (e.g. patient 3-15m is an EDGE while quick <3m is a LEAK), call it out:

```
DOUBLE PLAY: shifting from <leak slice> to <edge slice> wins twice — 
removes -$X leak AND amplifies the +$Y edge.
```

### 6. Update suggestion

End with:

```
To make this analysis the source of truth, run on PC:
  python ~/mes-intel/tools/leak_detector.py

The output of this skill should match. If not, the journal CSV may be stale — 
run the rithmic_importer.py on the PC to refresh.
```

## Output format

Headline (total leak $ identified) → 3-5 leak blocks → Easiest dollar → Double play if applicable → PC tool reference.

## Don'ts

- Never call a leak with N < 3. Even N=3 should be flagged "low confidence."
- Don't prescribe more than 1-2 behavioral fixes at a time. Compounding requires focus.
- Don't conflate variance with leaks. If a slice has high variance but break-even avg, it's noise, not a leak.
- Don't include PERSONAL trades. Each account has its own leak profile.
