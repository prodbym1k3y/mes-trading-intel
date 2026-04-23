---
name: prop-firm-status
description: Live APEX-565990-01 account state — current balance, distance to DD bust ($48k), distance to pass (+$3k), week-to-date P&L, commission burden, and posture verdict. Mirrors `drawdown_monitor.py`. Trigger phrases include "drawdown check", "where am I", "account status", "am I in trouble", "apex status", "balance check", "prop firm status".
---

# Prop Firm Status — APEX Live Account State

Quick-glance account health. The only number Jaime cares about minute-to-minute. Mirrors `python ~/mes-intel/tools/drawdown_monitor.py`.

## Required context

Always load `mes-context` first. Account: **APEX-565990-01** only (PERSONAL never enters this skill).

## Workflow

### 1. Compute current balance

Read `journal/all_trades.csv`, filter to `APEX-565990-01`, ORDER BY `entry_time DESC`. Take the latest `cum_pnl` value:

```
Current balance = $50,000 + last_cum_pnl
```

### 2. The numbers

```
APEX-565990-01 — as of <Phoenix datetime>

Balance:           $XX,XXX
Cumulative P&L:    $±X,XXX (since 2026-04-13)
Distance to BUST:  $X,XXX  (DD bust at $48,000 trailing)
Distance to PASS:  $X,XXX  (target +$3,000 NET = $53,000)

This week (Mon-Fri to date): $±X (N trades, N sessions)
Today's session:             $±X (N trades)
```

### 3. Posture

| Condition | Posture | Color |
|---|---|---|
| Balance < $48,500 | **EMERGENCY** — sim-only today, no live trades | RED |
| Balance < $49,000 | **DEFENSIVE** — 5ct max, A-grade only, single trade then walk if losing | RED |
| Balance $49,000 – $49,750 | **CAUTIOUS** — normal rules, tight stops, no escalation | YELLOW |
| Balance $49,750 – $52,500 | **STANDARD** — full rules in play | GREEN |
| Balance $52,500 – $52,900 | **PASS-APPROACH** — within $500 of pass — A-grade only, walk after first +1R | YELLOW |
| Balance ≥ $53,000 | **PASS** — stop trading the eval. Withdraw or move to funded account | GREEN |

### 4. Commission burden

```
Lifetime commissions on APEX: $X
% of gross profit consumed:    Y%
```

If Y > 100% (the canonical case from week 1): "Commissions are eating your gross. The single highest-leverage fix is removing sub-6-tick scalps."

### 5. DD trajectory

Compute the rolling peak of `cum_pnl` and the trailing DD from peak. Render:

```
Highest peak balance: $X,XXX (date)
Drawdown from peak:   $X,XXX
% of $2,000 buffer used: Y%
```

If buffer-used > 50%: flag "Half the DD buffer is gone. One bad day from danger zone."

### 6. Pass-at-pace projection

Read the last 10 sessions. Compute average net per session (post-commissions). Project:

```
Avg net/session (last 10): $X
Sessions remaining to pass at this pace: N

If only A/B sessions traded (recent A/B avg net: $Y):
  Sessions remaining: M  (~M÷5 weeks)
```

The "if only A/B" math is the single most motivating number for Jaime — always include it.

## Output format

Tight 20-30 line block. The user reads this multiple times per day. Headlines first, details after, posture verdict at the bottom.

## Don'ts

- Don't compute or display PERSONAL balance — different account, different rules.
- Don't recommend specific trades.
- Don't soften an EMERGENCY/DEFENSIVE posture. The whole point is to respect the brake.
- Don't speculate on whether he'll pass — only project at current pace and at A/B-only pace.
