---
name: weekly-review
description: Comprehensive weekly retrospective across all MES trading. Aggregates daily grades, regime distribution, account growth trajectories, edge cases, and behavioral patterns. Heavier than /grade-day — meant for Sunday/Saturday review sessions. Trigger phrases include "weekly review", "review this week", "weekly retro", "how was my week", "Sunday review".
---

# Weekly Review — The Bigger Picture

Once a week the trader steps back from individual sessions and looks at the shape of things. This skill synthesizes 5 RTH sessions of data into trends, regime dynamics, and structural observations that don't show up in any single day.

## Required context

Load `mes-context` first. Use `mes-brain` for queries. Use `brain-files` to check Obsidian for prior weekly notes.

## Inputs to gather

- **Week boundaries** — default to last 5 RTH sessions ending today. Allow user to specify a different week.
- **Comparison baseline** — default to prior 4 weeks rolling.

## Workflow

### 1. Top-line numbers per account

For each account:

```
Trades: N (vs baseline avg M)
Win rate: X% (vs Y%)
Net P&L: $A (vs $B)
Profit factor: <pf>
Max DD this week: $C
Equity now vs week start: +/-$D
```

### 2. Regime distribution

Count time spent in each regime this week (use `market_regimes` time-series). Then bucket trades by regime at entry. Compute per-regime win rate and avg P&L. Render:

| Regime    | Time in regime | Trades | WR  | Net  |
|-----------|----------------|--------|-----|------|
| trending  | 4h 12m         | 8      | 62% | +$X  |
| ranging   | 9h 30m         | 12     | 41% | -$Y  |
| ...       |                |        |     |      |

This is where structural insights live — "I made all my money in trending and gave it back in ranging" is a high-value finding.

### 3. Strategy contributions

Pull `strategy_weights_history` for the week's start and end. List strategies whose weight rose >20% (system is leaning into them) and those that dropped >20% (system is muting them). Cross-reference with the strategies that triggered actual trades — are you taking the ones the system likes?

### 4. Time-of-day breakdown

Bucket trades by 30-minute window from the open. Win rate and avg P&L per bucket. Identify the trader's:

- **Money window** — bucket(s) with the best expectancy
- **Drain window** — bucket(s) with negative expectancy
- **Inactivity** — buckets with zero trades that historically had good edge

### 5. Behavioral patterns

Look for:

- **Revenge trading** — clusters of trades within 5 min of a loss with negative outcomes.
- **Overtrading days** — days with N >> baseline avg, broken down by outcome.
- **Best-day breakdown** — if one day made the week, what was different about it?
- **Worst-day breakdown** — if one day broke it, was it a process failure or a market environment issue?

### 6. Open questions

Surface 2-3 questions the data raises that need a human decision next week. Examples:

- "Strategy X has been triggering more but losing. Manually review weights?"
- "All wins came when MarketBrain said `trending`. Should we gate harder on regime confidence?"
- "Prop firm A is 80% to profit target but on a losing streak — slow down or push through?"

### 7. Suggested capture

End by suggesting the user run `/insight-capture` on the single most important finding from the week. Provide a draft so they can confirm or edit.

## Output format

Sections in order: **Headline**, **By account**, **By regime** (table), **Strategies in play**, **Time-of-day**, **Behavioral notes**, **Open questions**, **Suggested capture**.

Length budget: 60-90 lines of markdown. This is the meatiest skill but should still fit on 2-3 screens.

## Don'ts

- Don't claim a behavior is a "leak" with N < 5. Patterns need a sample.
- Don't repeat what `/grade-day` already said for each day. Aggregate, don't restate.
- Don't add motivational summary. This is analysis, not coaching.
