---
name: grade-trade
description: Deep grade of a single MES trade. The trader provides the trade (or a journal_trades id) and this skill reconstructs what the system was saying at entry, evaluates execution quality, and proposes a specific lesson. Trigger phrases include "grade this trade", "review my last trade", "what did the system say when I entered", "was that a good entry".
---

# Grade Trade — Single Trade Deep Review

For when the trader wants surgical analysis of one trade — usually right after closing it, or when reviewing a notable winner / loser.

## Required context

Load `mes-context` first. Use `mes-brain` MCP for queries.

## Inputs to gather

Ask the user for whichever of these is missing (use AskUserQuestion if multiple are missing):

1. Trade identifier — either a `journal_trades.id` or `entry_time` + `side`.
2. If the trade isn't in the journal yet: side, size, entry price, exit price (or "still open"), entry time, account.

If the trade IS in `journal_trades`, pull the full row and skip asking.

## Workflow

### 1. Reconstruct the moment of entry

For the entry timestamp:

- Pull the regime active at that moment from `market_regimes`.
- Pull the most recent ensemble snapshot before entry from `agent_knowledge` / `learning_history`.
- Pull confluence zones within ±5 points of entry price from `confluence_zones`.
- Pull dark pool prints in the 30 minutes before entry from `dark_pool_prints`.
- Pull strategy weights active at entry from `strategy_weights_history`.

### 2. Grade across 6 dimensions

| Dimension              | Question to answer                                                            |
|------------------------|-------------------------------------------------------------------------------|
| **System alignment**   | Did the ensemble agree with the trade direction? Confidence ≥ min_confidence? |
| **Regime fit**         | Is this strategy class historically profitable in this regime?                |
| **Level quality**      | Was entry near a confluence zone, or in no-man's-land?                        |
| **Order flow context** | Dark pool / delta direction supportive?                                       |
| **Risk:Reward**        | What was the planned R, what was the realized R?                              |
| **Execution quality**  | Slippage from signal trigger? Time in trade vs setup horizon?                 |

Each gets a one-line verdict and a letter grade.

### 3. Compare to similar historical trades

Find the 5 closest matches in `journal_trades`:
- Same side, same regime, similar entry distance to a confluence zone.
- Compute average outcome of those matches.
- "This trade vs your average for this setup: <better|same|worse> by $X".

### 4. The lesson

End with ONE specific lesson, framed as a forward-looking rule:

- "When ensemble confidence is below 0.65 in a ranging regime, wait for a re-test." (if low alignment and bad outcome)
- "This is your A-setup — when ensemble + zone + dark pool all agree, size up next time." (if high alignment and good outcome)

If the trade is still open, end with a different block instead:

```
Status: open
Hold thesis: <still valid|degrading|invalidated>
Trail to: <specific price>
Invalidation: <specific price>
```

## Output format

Header with trade summary (1 line). Then the 6-row grade table. Then the comparison. Then the lesson block. Total length: under 250 lines of markdown.

## Don'ts

- Don't grade the outcome — grade the decision. A losing trade with great alignment is a different problem than a winning trade with poor alignment.
- Don't speculate beyond the data. If the brain doesn't have an ensemble snapshot at entry, say so and skip that dimension.
