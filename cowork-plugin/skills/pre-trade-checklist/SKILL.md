---
name: pre-trade-checklist
description: Pre-entry friction check for an Apex trade. Mirrors the PC tool `pretrade.py` — asks for setup/edge/reason and flags any rule risk before Jaime pulls the trigger. Refuses trades under 6-tick edge or where current state breaches eval rules. Trigger phrases include "pre-trade", "checklist", "before I take this", "should I take this", "friction", "pretrade".
---

# Pre-Trade Checklist — Friction Before the Click

This is the brake. Goal: surface any "wait, no" reason in seconds before Jaime risks money. **It is friction, not a signal.** Even if every check is green, the answer is "your call" — never "go."

## Required context

Always load `mes-context` first. The authoritative version on PC is `python ~/mes-intel/tools/pretrade.py`. Suggest that tool if Jaime is at his PC — it has live ES context.

## Inputs to gather

Use AskUserQuestion to collect (skip any the user already provided):

1. **Direction** — long / short
2. **Contracts** — 1 / 2 / 5 / 7
3. **Setup** — one-line description (e.g. "VWAP reclaim after 5-min consolidation", "GEX wall fade", "tape shift after sweep")
4. **Edge in ticks** — Jaime's expected R-multiple stop and target (in ticks)
5. **Reason this is a trade** — one sentence

If the edge in ticks is < 6, **stop immediately** and refuse: "Trade refused. <6-tick edge has negative expectancy after $1.34/RT × N contracts commission. See commission_filter.py for size math."

## Workflow — run all checks, fail fast

### CHECK 1: Account state

Read `journal/all_trades.csv` filtered to `APEX-565990-01`. Compute current balance.

- If balance < $48,500 (within $500 of bust) → **HARD STOP** "Within $500 of $48k bust. No live trade today."
- If balance < $49,000 → **YELLOW** "DD buffer thin. 5-contract max."

### CHECK 2: Today's session state

Compute today's session metrics from `journal/all_trades.csv` (today's session_date):

- Today's trade count, today's net P&L
- Last 5-min: was there a loss? (For revenge-trade flag.)

Rules:
- Today's trade count >= 10 → **HARD STOP** "Daily cap (10 trades) reached."
- Today's net P&L < -$300 → **HARD STOP** "Daily loss cap reached. Walk."
- Last 5 minutes had a loss AND current entry is same direction → **YELLOW** "Same-side re-entry within 5min of a loss is the #5 leak (no_revenge rule)."

### CHECK 3: Size discipline

Compare requested contracts vs the size locked at session start (assume first trade's size is "session lock"). If escalating mid-session (e.g. session started at 5, now requesting 7) → **YELLOW** "Mid-session size escalation flagged. The 10-pt hold pattern is from 5-7ct, but size up only AFTER green and patient holds."

### CHECK 4: Time-of-day fit

Current hour ET:
- 06:00-06:59 ET (06h is the #5 leak, -$295) → **YELLOW** "06h is your #5 leak."
- 09:30-09:59 ET (opening cap) → **YELLOW** "Opening 30min — opening_cap rule applies. Max 2 trades."
- Day of week == Monday → **YELLOW** "Monday is your worst day historically (-$446)."
- 09:41-11:00 ET window AND not first trade → **GREEN PROMOTE** "This is the proven 10-pt hold window."

### CHECK 5: Edge math

Commission cost:
```
Round-trip cost = $1.34 × contracts
Break-even ticks = ceil(round-trip / ($1.25 × contracts))
```

If requested edge_ticks < (break-even + 4 ticks safety margin) → **YELLOW** "Edge is barely above break-even after commissions. Either tighter stop or bigger target."

### CHECK 6: Setup quality vs current edges

Match the setup against `claude-memory/project_current_edges.md`. If the slice matches a known edge (e.g. patient hold 3-15m, trade #3, 08h hour) → flag green. If it matches a leak slice (e.g. quick <3m scalp, short-midday, 5ct) → flag yellow.

## Output

```
PRE-TRADE CHECK — <direction> <contracts>ct on APEX-565990-01

Account state:    <green | YELLOW | HARD STOP> — <one line>
Session state:    <green | YELLOW | HARD STOP> — <one line>
Size discipline:  <green | YELLOW> — <one line>
Time-of-day:      <green | YELLOW | GREEN PROMOTE> — <one line>
Edge math:        <green | YELLOW> — <break-even N ticks, requested M ticks>
Setup vs edges:   <green | YELLOW> — <which slice matched>

VERDICT: <PROCEED | WAIT | NO-TRADE>
Note: <one sentence summarizing why>
```

`PROCEED` only if zero HARD STOP and at most one YELLOW.
`WAIT` if 2+ YELLOWs or one HARD STOP that may clear.
`NO-TRADE` if any HARD STOP that won't clear without changing the day's situation.

If verdict is PROCEED: end with "Your call. The data supports it. Take it on YOUR terms, not the system's." (Reinforces feedback-not-signal philosophy.)

## Don'ts

- Never say "go" or "take it" — always "your call" even on green.
- Don't run heavy queries — this needs sub-3-second responses.
- Don't recommend specific entry / stop / target prices.
- Don't override Jaime's setup judgment with system opinion. Surface risks, leave the call.
