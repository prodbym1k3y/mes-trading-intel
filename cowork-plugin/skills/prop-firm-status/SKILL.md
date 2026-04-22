---
name: prop-firm-status
description: Audit each prop firm account's compliance and progress. Checks current drawdown, daily loss usage, profit target progress, scaling milestones, and any rule risks. Critical pre-trade safety check across multi-account trading. Trigger phrases include "prop firm status", "account check", "am I in trouble on any account", "drawdown check", "what's left on profit target".
---

# Prop Firm Status — Multi-Account Compliance Snapshot

The trader runs MES on personal + multiple prop firm accounts. Each prop firm has different rules: max daily loss, total drawdown, profit target, scaling thresholds. This skill turns the journal data into a per-account safety dashboard.

## Required context

Load `mes-context` first. Use `mes-brain` MCP for all queries.

## Inputs to gather

If account configurations aren't already documented in the brain DB (look in `agent_knowledge` for keys like `account_*` or check if there's an `accounts` or `prop_firm_rules` table), ask the user to confirm or provide:

- Account name / id
- Firm
- Starting balance / equity
- Max daily loss ($)
- Max trailing drawdown ($)
- Profit target ($)
- Current phase (evaluation / funded / scaled)
- Position size limits

Persist this to `agent_knowledge` after the user confirms (key pattern: `account_<id>_rules`) so future runs don't re-ask.

## Workflow

### 1. Pull trades per account

Query `journal_trades` grouped by `account_id` (or whatever account-tag column exists). Per account:

- All-time trade count
- Today's trades and today's net P&L
- Last 5 trading days net P&L
- Realized P&L since account opened
- Max drawdown from peak equity

### 2. Per-account compliance row

For each account, render:

```
Account: <name>  ·  Firm: <firm>  ·  Phase: <phase>
Equity: $XXX,XXX (Δ from start: +/-$Y)
Today's P&L: $Z (limit: -$W)  →  USED <pct>% of daily loss budget
Drawdown from peak: $A (max: $B)  →  USED <pct>% of total DD budget
Profit target: $C / $D  →  <pct>% complete
Posture: <green|yellow|red>
```

Color rules:
- **Green**: <50% of any limit used, profit target progress on track.
- **Yellow**: 50-80% of any limit used, OR profit target stalling.
- **Red**: >80% of any limit used, OR within 1 average losing day of breaching.

### 3. Cross-account trade-coordination warnings

If the trader holds correlated positions across accounts (e.g. long MES on personal + long MES on a prop firm), flag total notional and aggregate risk. Many prop firms forbid copy-trading across accounts at the same firm — note if relevant.

### 4. Restrictions for today

End with an actionable block per red/yellow account:

```
<account>: <restriction>
  e.g. "Stop trading after 1 losing trade today — drawdown buffer is one bad fill from breach."
  e.g. "Take only A-graded setups (run /grade-trade beforehand) — profit target requires +$400 from 2 trades."
```

If all accounts are green, just say "All accounts green. Trade your normal book."

## Output format

One row per account, then a restrictions block at the bottom. Tight, scannable, color-coded with emoji or text labels (no need for fancy UI).

## Don'ts

- Don't render raw trade lists — this is a status summary, not a journal.
- Don't recommend overriding a prop firm rule, ever. If a rule is being approached, the answer is restraint, not creativity.
- Don't ignore stale rule configs. If `agent_knowledge` has rules from >60 days ago and the user confirms they renewed/changed plans, update the stored rules.
