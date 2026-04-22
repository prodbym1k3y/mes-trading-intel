---
name: playbook-match
description: Pattern-match the current MES setup against historical trades and patterns in the brain DB. Finds the closest 5-10 prior setups, summarizes their outcomes, and surfaces what the system has learned about this pattern type. Trigger phrases include "find similar setups", "have I traded this before", "playbook check", "pattern match", "what's the historical edge here".
---

# Playbook Match — Pattern Recognition Against History

The trader sees something forming and wants to know: have we been here before, and what happened?

## Required context

Load `mes-context` first. Use `mes-brain` MCP for queries.

## Inputs to gather

Ask for any of these the user hasn't already provided:

- **Side** considered (long / short)
- **Setup type** (e.g. "VAL reclaim", "FVG fill", "delta divergence", "GEX wall rejection", "premarket sweep")
- **Current regime** (or have the skill pull the latest from `market_regimes`)
- **Entry zone** (price level or confluence zone reference)

## Workflow

### 1. Search market_patterns

Query `market_patterns` for entries matching the setup type. If the table uses tags or a description field, do a fuzzy match. For each match, note:

- Pattern definition.
- Historical occurrence count.
- Win rate when triggered.
- Average $ outcome.
- Best regime fit.

### 2. Search journal_trades for similar past trades

Filter by:

- Same `side`.
- Same regime (join against `market_regimes` by timestamp proximity).
- Entry price near a similar confluence zone composition (if the zones are tagged with factor types in `confluence_zones`).
- Optionally: similar time-of-day band (open / midmorning / midday / afternoon).

Order by recency. Take the top 10. For each, render a one-line summary: `[date] [side] [size] [+/-$] [held N min] [setup tag] [outcome note]`.

### 3. Statistical summary

```
Matches: N
Win rate: X%
Avg outcome: $Y
Best case: $Z
Worst case: $-W
Best regime fit: <regime>
Avg time in trade: M min
```

### 4. What the agents have learned

Query `learning_history` for entries tagged with this pattern type. Render any specific lessons (one-liners). Also pull `agent_knowledge` for any key matching the pattern — the MetaLearner often stores aggregate observations there.

### 5. Verdict

Three-line block:

```
Edge: <strong|moderate|weak|negative>
Best version of this trade: <description of the conditions for highest historical win rate>
Avoid if: <conditions that historically broke the pattern>
```

## Output format

In order: stats summary, top 10 historical matches table, lessons learned, verdict.

## Don'ts

- Don't claim a pattern has edge if N < 10 historical occurrences. Say "insufficient sample" instead.
- Don't conflate the AI's confidence with edge. Edge is a function of historical win rate and expectancy, not the model's internal confidence score.
- Don't suggest sizing — that depends on account, drawdown state, and prop firm rules.
