---
name: pre-trade-checklist
description: Fast pre-trade safety check before pulling the trigger on an MES position. Verifies prop firm headroom, regime alignment, ensemble confidence, recent same-side trades, and conflict with active confluence levels. Trigger phrases include "before I take this", "checklist", "should I pull the trigger", "pre-trade check", "safe to enter".
---

# Pre-Trade Checklist — Don't-Press-The-Button-Without-This

The trader has a setup forming and is about to enter. This skill is the brake — a 10-second sanity check that surfaces any "wait, no" reasons before money is at risk.

## Required context

Load `mes-context` first. Use `mes-brain` for queries.

## Inputs to gather

Ask only what isn't already in conversation:

- **Side** (long / short)
- **Account** to trade on (personal / propA / propB / etc.)
- **Approximate entry price** (or "at market")
- **Approximate stop** (in points or dollars)

Skip these if obvious from context.

## Workflow — run in parallel, fail fast

### CHECK 1: Prop firm headroom

Pull the account's current state from `journal_trades` (today's net + total drawdown from peak).

- **Daily loss budget remaining** — if less than the planned stop, **HARD STOP**.
- **Total drawdown buffer remaining** — if less than 2x the planned stop, flag yellow.
- If account is in a "no trade after losing" rule period (e.g. 1-loss-then-stop), and there's a loss today, **HARD STOP**.

### CHECK 2: Regime alignment

Pull current regime from `market_regimes`. Pull strategy weights from `strategy_weights_history`.

- If the strategies that justify this trade direction are currently **muted** (low weight) for this regime → flag yellow.
- If regime confidence is below 0.6 → flag yellow ("system is uncertain about conditions").

### CHECK 3: Ensemble confidence

Pull the most recent ensemble snapshot.

- If number of strategies agreeing < `min_strategies_agree` (from config) → flag yellow.
- If aggregate confidence < `min_confidence` (from config) → flag yellow.
- Direction conflict (system bias opposite the trade) → **HARD STOP** unless user explicitly notes they're trading against system on purpose.

### CHECK 4: Recent same-side trades

Query `journal_trades` for trades in the last 4 hours, same side. If 2+ losses in a row this side → flag yellow ("you're tilting on this side, take a beat").

### CHECK 5: Confluence proximity

Query `confluence_zones` for zones within ±3 points of the planned entry.

- Trading INTO a strong confluence wall (3+ factors against you within 3 pts) → flag yellow.
- No nearby confluence support (closest zone >10 pts away on supportive side) → flag yellow ("no level to lean on").

## Output

Render in this exact format — fast scan:

```
PRE-TRADE: <side> on <account>

Prop firm:    <green|YELLOW|HARD STOP> — <one line>
Regime:       <green|YELLOW|HARD STOP> — <one line>
Ensemble:     <green|YELLOW|HARD STOP> — <one line>
Recent:       <green|YELLOW|HARD STOP> — <one line>
Levels:       <green|YELLOW|HARD STOP> — <one line>

VERDICT: <GO | WAIT | NO>
Reason: <one sentence>
```

`GO` only if zero HARD STOP and at most one YELLOW.
`WAIT` if 2+ YELLOWs or one HARD STOP.
`NO` if any HARD STOP that won't clear without major change (e.g. drawdown breached).

## Don'ts

- Don't run any check that takes more than a couple of queries — this needs to return in seconds.
- Don't second-guess a HARD STOP. The whole point of the checklist is that the trader respects it.
- Don't suggest position sizing — that's a separate skill if needed.
