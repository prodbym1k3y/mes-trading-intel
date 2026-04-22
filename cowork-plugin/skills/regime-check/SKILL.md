---
name: regime-check
description: Live snapshot of current MES market regime, signal engine ensemble score, dark pool activity, GEX dealer positioning, and recommended posture. Use during the trading session when the trader asks "what's the regime", "is this trendable", "should I be in this", "what are the agents saying right now", or "regime check".
---

# Regime Check — Live Trading Snapshot

The trader is at the screen, has a setup forming or a position on, and wants the system's read in 5 seconds. This skill reads the most recent state from the brain DB and renders a tight verdict.

## Required context

Load `mes-context` first. Use `mes-brain` MCP for all queries.

## Workflow

### 1. Most recent regime

Query `market_regimes` ORDER BY timestamp DESC LIMIT 1. Report:

- Regime name and confidence.
- Time since regime change (Phoenix time).
- Hurst exponent if available (>0.5 trending, <0.5 mean-reverting, ≈0.5 random walk).

### 2. Signal engine current state

The SignalEngine writes ensemble scores to either `agent_knowledge` (key like `last_ensemble`) or has a recent entry in `learning_history`. Find the most recent ensemble snapshot. Report:

- Number of strategies agreeing.
- Aggregate confidence.
- Direction (long bias / short bias / neutral).
- Top 3 contributing strategies by individual confidence.

### 3. Dark pool pulse

Query `dark_pool_prints` for the last 60 minutes. Report:

- Print count.
- Total notional.
- Direction skew if inferable (block prints near bid vs ask, or above/below VWAP).
- Largest single print and its proximity to current price.

### 4. Confluence zones in play

Query `confluence_zones` for the closest 3 levels above and 3 below the last known mark. Don't list more — clutter kills usefulness intra-session.

### 5. Agent accuracy drift

Query `agent_accuracy` filtering to today's session. If any agent's accuracy has dropped >15% from its 30-day baseline, flag it — the system is in unusual conditions for that agent.

### 6. The verdict

End with a single block:

```
Posture: <go|wait|stand-aside>
Why: <one sentence>
Invalidation: <specific price or condition>
```

`go` = the system is aligned for active trading. `wait` = mixed signals, hold off. `stand-aside` = conditions are bad, no trade.

## Output format

One screen. Use a small table for the regime + ensemble row, bullet list for dark pool + zones, then the verdict block at the bottom.

## Don'ts

- Don't run analyses that take more than a few queries — the trader is live and waiting.
- Don't recommend specific trade sizing — that's account- and risk-tolerance-specific.
- Don't sugar-coat a `stand-aside` call. The system says no trade, you say no trade.
