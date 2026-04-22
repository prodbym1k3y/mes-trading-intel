---
name: morning-prep
description: Generates a pre-market trading briefing for MES futures. Pulls overnight ranges, key levels, regime context, GEX dealer positioning, news catalysts, prop firm constraints, and what worked / failed in recent sessions. Trigger phrases include "morning prep", "premarket", "what's the setup today", "brief me before the open", "morning briefing for MES".
---

# Morning Prep — MES Pre-Market Briefing

Generate a focused, scannable briefing the trader reads before the 6:30 AM Phoenix RTH open. Pull every signal from the brain DB, then render a single concise report.

## Required context

Load the `mes-context` skill first for instrument specs, session times, and DB schema. Use the `mes-brain` MCP server for all queries.

## Workflow

### 1. Establish "today"

- Today's date in Phoenix time (`America/Phoenix`).
- Identify the prior RTH session date (skip weekends, holidays).

### 2. Recent performance digest

Query `journal_trades` for trades in the last 5 RTH sessions:

- Total trades, win rate, gross P&L, avg R, largest win, largest loss.
- Group by `account_id` (or whatever the account tag column is) so the personal account and prop firms are separated.
- Flag any prop firm account that is within 30% of its drawdown limit or 80% of its profit target — these are state-critical and the trader needs to know before placing the first trade.

### 3. Regime carryover

Query `market_regimes` for the most recent regime entry. Report:

- Current regime name (trending/ranging/volatile/quiet/breakout).
- How long has it persisted (in hours).
- Hurst exponent if logged.
- Recent regime transitions in the last 48 hours.

### 4. Key levels for today

Query `confluence_zones` for active levels (filter to those where the level is within ±50 points of last close). Report each as:

- Price level, factor count, dominant factors (e.g. "VAH + GEX flip + prior day low").
- Distance from last settlement.

If `dark_pool_prints` has activity from yesterday's RTH, summarize it: total notional, average size, any clusters near a confluence zone.

### 5. Strategy + agent state

Query `agent_accuracy` for the last 7 days. List the top 3 strategies by recent accuracy and the bottom 3. The MetaLearner weights these via `strategy_weights_history` — pull the most recent weight snapshot and show what is currently being upweighted vs muted.

### 6. News + catalysts

If `learning_history` has entries from the NewsScanner in the last 18 hours, include them. Also flag known economic events for today (CPI, FOMC, NFP) — ask the user if you don't have a calendar source connected.

### 7. The actual recommendation

End with a 3-line "morning posture" summary:

```
Bias: <bullish|bearish|neutral|wait-for-confirmation>
Posture: <aggressive|standard|defensive|sit-out>
Watch: <one specific level or trigger that would change the call>
```

This is the part the trader wants. Make it specific. "Wait for confirmation above 5240 with positive delta and CD trending up" beats "be cautious."

## Output format

A single markdown report. Sections in this order: **Top of mind** (the 3-line posture), **Account state**, **Levels**, **Regime**, **Edge today** (which strategies are hot), **Catalysts**, **Yesterday recap**.

Keep it under one screen. The trader will read this in 60 seconds before the open. Brevity is the brief.

## Don'ts

- Don't predict direction with false confidence. Frame as conditional ("if X, then Y").
- Don't repeat raw DB rows — synthesize.
- Don't include sections that have no data — omit them with a one-line "no signal" note instead.
- Don't recommend overriding the prop firm rules. If an account is constrained, surface it loudly.
