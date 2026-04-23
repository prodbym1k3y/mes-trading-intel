---
name: grade-trade
description: Deep grade of a single MES trade — surgical review combining system reconstruction (regime, ensemble, zones, flow) with psychological state assessment (triggers, biases, arc position). Trigger phrases include "grade this trade", "review my last trade", "what did the system say when I entered", "was that a good entry".
---

# Grade Trade — Single Trade Deep Review

For when the trader wants surgical analysis of one trade — usually right after closing it, or when reviewing a notable winner / loser.

## Required context

1. Load `mes-context` — instrument specs, schema.
2. Load `brain-files` → read `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` in full. This is the behavioral anchor — every lesson must be framed against it.
3. Use `mes-brain` MCP for DB queries.

## Inputs to gather

Ask only for what's missing (use AskUserQuestion if multiple are missing):

1. Trade identifier — either a `journal_trades.id` or `entry_time` + `side`.
2. If the trade isn't in the journal yet: side, size, entry price, exit price (or "still open"), entry time, account.

If the trade IS in `journal_trades`, pull the full row and skip asking.

## Workflow

### 1. Reconstruct the moment of entry

For the entry timestamp:

- Regime active at that moment from `market_regimes`.
- Most recent ensemble snapshot before entry from `agent_knowledge` / `learning_history`.
- Confluence zones within ±5 points of entry price from `confluence_zones`.
- Dark pool prints in the 30 minutes before entry from `dark_pool_prints`.
- Strategy weights active at entry from `strategy_weights_history`.
- **State at entry**: seconds since last exit, last trade's outcome, session P&L at entry, trade number of session, Phoenix hour.

### 2. Grade across 7 dimensions (system + psychology)

| Dimension              | Question                                                                          |
|------------------------|-----------------------------------------------------------------------------------|
| **System alignment**   | Did the ensemble agree? Confidence ≥ min_confidence?                              |
| **Regime fit**         | Is this strategy class historically profitable in this regime?                    |
| **Level quality**      | Was entry near a confluence zone, or in no-man's-land?                            |
| **Order flow context** | Dark pool / delta direction supportive?                                           |
| **Risk:Reward**        | Planned R vs realized R?                                                          |
| **Execution quality**  | Slippage from signal trigger? Hold time vs setup horizon?                         |
| **🧠 Psych state**     | Which trigger (if any) was firing? Which of "Two Jaimes" took this trade?         |

Each gets a one-line verdict and a letter grade.

### 3. Name the state that took the trade

Use the psychology-profile as the vocabulary. Classify the entry as one of:

- **Patient Jaime** — No triggers firing, within edge window, size planned pre-market. Positive edge regardless of outcome.
- **Triggered Jaime — Revenge** — Entered within 5 min of a loss. Win rate on this state is 26–29%. Even if it won, it was a negative-EV decision.
- **Triggered Jaime — Overtrade** — Session trade count ≥ 10 at entry. Selectivity was already blown.
- **Triggered Jaime — Hole-chase** — Session P&L ≤ -$50 at entry. Entered to get back, not from edge.
- **Triggered Jaime — Quick-grab** (on exit) — Unrealized gain exited before structure target. Applies to exit grading, not entry.
- **Triggered Jaime — Long-bias default** — Long trade with no strong signal, in a session where >75% of trades were long.

If multiple, list them.

### 4. Compare to similar historical trades

Find the 5 closest matches in `journal_trades`:
- Same side, same regime, similar entry distance to a confluence zone, **similar state** (revenge / patient / etc.).
- Compute average outcome.
- "This trade vs your average for this setup *in this state*: <better|same|worse> by $X."

Framing matters: the comparison should be against **same-state** historical trades, not all same-setup trades. A great setup taken in a revenge state has a different base rate than the same setup taken patient.

### 5. The lesson

End with ONE forward-looking rule. Two templates:

**If Patient Jaime took it (regardless of outcome):**
> "This is your A-setup — ensemble + zone + flow + calm state all aligned. When all four agree, size up to your max size next time."

**If Triggered Jaime took it:**
> "The setup was [valid/invalid], but the state was [trigger]. The decision-quality issue is [trigger], not the setup. Next intervention: [specific physical action from the profile]."

If the trade is still open, replace the lesson with:

```
Status: open
Hold thesis: <still valid|degrading|invalidated>
Trail to: <specific price from structure, not dollar amount>
Invalidation: <specific price>
Psych check: <any trigger currently firing? If YES, close half now and re-assess.>
```

## Output format

1. One-line trade summary.
2. 7-row grade table.
3. State classification ("This was taken by [Patient/Triggered] Jaime — [which trigger]").
4. Same-state comparison.
5. Lesson block.

Total: under 250 lines. Grade the decision, not the outcome.

## Don'ts

- Don't grade emotionally. A losing trade from Patient Jaime is a cost of doing business. A winning trade from Triggered Jaime is a near-miss, not a win.
- Don't speculate beyond the data. If the brain has no ensemble snapshot at entry, say so and skip that dimension.
- Don't soften the state classification. If revenge was firing, name it. Gentle coaching is what got him here.
- Don't recommend strategy changes — that's the MetaLearner's job. Grade execution and state.

---
*Anchors on: `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md`*
