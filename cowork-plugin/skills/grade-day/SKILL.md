---
name: grade-day
description: End-of-day quantitative review of MES trading performance. Grades each trade against the system's signals, agent agreement, and regime context. Surfaces what the trader did well, what was suboptimal, and what to adjust tomorrow. Trigger phrases include "grade my day", "EOD review", "how did I do today", "post market review", "wrap the day".
---

# Grade Day — End-of-Day Performance Review

This is the daily reflection ritual. The goal is not to make the trader feel good or bad — it's to surface concrete patterns that compound into improvement.

## Required context

Load `mes-context` first. Use `mes-brain` MCP for all queries. Use today's Phoenix date as the session boundary unless the user specifies a date.

## Workflow

### 1. Pull today's trades

Query `journal_trades` for entries in today's RTH window (6:30 AM – 2:00 PM Phoenix). Order chronologically. For each trade, capture: entry time, exit time, side, size, instrument, entry price, exit price, P&L (in $ and points), AI grade if present, regime at entry, account.

If there were no trades, end here with: "No trades today. State: <flat>. Want to run /morning-prep for tomorrow instead?"

### 2. Headline numbers

```
Trades: N
Win rate: X%
Net P&L: $Y (Z points)
Avg winner: $A   Avg loser: $B
Profit factor: <gross_wins / gross_losses>
Max drawdown intraday: $C
By account: personal $X | propA $Y | propB $Z
```

### 3. Per-trade grading

For each trade, do a 4-axis grade A/B/C/D/F:

| Axis              | What it measures                                                                |
|-------------------|---------------------------------------------------------------------------------|
| **Setup**         | Was the system aligned at entry? (regime + ensemble agreement + zone proximity) |
| **Entry**         | Did you get a price near the signal trigger, or chase?                          |
| **Management**    | Did you respect the invalidation? Trail vs panic?                               |
| **Exit**          | Did you take what was there, or leave / overstay?                               |

Pull the regime at entry from `market_regimes` (closest record by timestamp). Pull ensemble state from `agent_knowledge` / `learning_history` near the entry time. Compare what the system was saying at the moment of entry to what the trader did.

Render as a compact per-trade row: `[time] [side] [size] [+/-$] [Setup/Entry/Mgmt/Exit grades] [one-line note]`.

### 4. Pattern recognition

Look across today's trades for repeating issues:

- All losses came from same regime / same time-of-day / same trigger type → flag.
- All wins came from one specific setup → flag (so they can lean into it).
- Entries during low ensemble confidence → flag.
- Trades during a regime the system was uncertain about → flag.

### 5. Comparison to baseline

Pull the last 20 RTH sessions from `journal_trades`. Compare today's win rate, profit factor, and avg R against the rolling baseline. Today is "above / at / below baseline" — say it plainly.

### 6. Tomorrow's adjustment

End with **one specific behavioral adjustment** for tomorrow, derived from the patterns above. Examples:

- "Skip the first 15 minutes — 3 of today's losses were premature opens."
- "Don't take counter-trend entries when MarketBrain regime confidence is below 0.6 — happened twice today, both lost."
- "Your A-grade setups returned $X today. Take only those tomorrow until the prop firm drawdown is reset."

ONE adjustment. Not five. Compounding requires focus.

## Output format

In order: **Headline numbers**, **Per-trade table**, **Patterns I noticed**, **Vs baseline**, **Tomorrow's adjustment**. End there.

## Don'ts

- Don't grade emotionally ("you traded well!"). Grade against the system.
- Don't recommend strategy changes — that's the MetaLearner's job. This skill grades execution.
- Don't write a wall of text. The trader will skim. Make the per-trade table the meat.
- Don't suggest more than one adjustment for tomorrow.
