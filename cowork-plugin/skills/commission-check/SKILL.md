---
name: commission-check
description: Commission stress-test for the Apex eval. Computes commission burden across recent trades, breaks down by contract size, and identifies the break-even tick threshold for any proposed size. Mirrors `commission_filter.py`. Trigger phrases include "commission check", "commission stress test", "what does it cost", "fees", "is this size profitable", "break-even ticks".
---

# Commission Check — Size Stress Test

The single biggest leak on the APEX eval is commission drag. Week 1 had gross +$213 destroyed by –$262 in commissions. This skill makes the math visible before sizing decisions get made.

## Required context

Always load `mes-context` first. Account: **APEX-565990-01**. Commission rate: **$1.34/RT/contract** via Rithmic. Authoritative version: `python ~/mes-intel/tools/commission_filter.py`.

## Workflow

### 1. Lifetime commission audit

Read `journal/all_trades.csv`, filter to APEX. Compute:

```
Total round-trips: N
Total commissions: $X
Total gross P&L: $Y
Net P&L: $(Y - X)
Commissions as % of |gross profit|: Z%
```

If Z >= 100%: "Commissions are eating ALL of your gross profit. Highest-leverage fix in your trading."

### 2. Per-contract-size breakdown

Group APEX trades by `contracts`. For each size:
```
1 contract:  N trades · gross $X · commissions $Y · net $Z · win rate W%
2 contracts: ...
5 contracts: ...
7 contracts: ...
```

Identify the size where net is most negative — that's where size discipline matters most.

### 3. Break-even tick math

Render the table for sizing decisions:

```
SIZE  ROUND-TRIP  BREAK-EVEN TICKS  BREAK-EVEN POINTS  SAFETY MARGIN (BE+4t)
1ct   $1.34       1.07              0.27               5 ticks
2ct   $2.68       1.07              0.27               5 ticks
5ct   $6.70       1.07              0.27               5 ticks
7ct   $9.38       1.07              0.27               5 ticks
```

Then explain in one line: "Break-even is tick-cost-per-contract independent of size — 1.07 ticks. The 6-tick rule already gives you 5x safety margin on commission alone."

### 4. The "sub-6-tick scalp" reality

Filter recent APEX trades where `ticks` is between 0 and 5 (small wins or scratches). Show net effect:

```
Sub-6-tick trades (last 30 days): N
Their NET P&L: $-X
Their gross P&L: $+Y
Their commission cost: $Z
If those trades had never been taken: APEX balance would be $(current + X) higher
```

This is the dollar number that should make scalps stop.

### 5. Stress test a hypothetical

If user provided a hypothetical size in their question (e.g. "is 7ct profitable for me"), compute:

```
At 7 contracts, your last N trades would have netted: $X (vs actual $Y at avg actual size)
```

If they didn't provide a hypothetical, prompt: "Want me to stress test a specific size or rule?"

## Output format

Audit headline → per-size breakdown → break-even table → sub-6-tick reality → optional stress test.

20-30 lines.

## Don'ts

- Don't recommend a specific contract size. Show math, leave the call.
- Don't include PERSONAL trades. (Commission rates were the same, but sizing context differs.)
- Don't soften the "commissions eat everything" conclusion when the data shows it.
