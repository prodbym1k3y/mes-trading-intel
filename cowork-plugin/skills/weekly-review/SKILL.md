---
name: weekly-review
description: Weekend retrospective across the Apex eval week. Aggregates daily grades, 10-rule rollups, P&L by day/regime/slice, commission drag, streak progression, and surfaces the structural lesson for next week. Mirrors `review_rotation.py` (themed weekly rotation). Trigger phrases include "weekly review", "weekend review", "review the week", "how was my week", "friday review".
---

# Weekly Review — Apex Eval Weekend Retrospective

The weekend ritual. Goal: extract the ONE structural observation this week that compounds into next week. Not a summary — a synthesis.

## Required context

Load `mes-context` first. Account: **APEX-565990-01**. Authoritative version on PC: `python ~/mes-intel/tools/review_rotation.py` (runs an 8-week themed rotation — each week emphasizes a different angle).

## Inputs

Default week: the just-completed Mon-Fri. User can override with explicit dates.

## Workflow

### 1. Weekly top-line

From `journal/all_trades.csv` filtered to APEX and this week:

```
Week of <Mon_date> – <Fri_date>

Sessions: N · Trade-days: M · Zero-trade days: <list>
Trades: N
Win rate: X% 
Gross: $A · Commissions: $B · Net: $C
Profit factor: <pf>
Worst intraweek drawdown: $D (<date>)

Balance: $E (start $F, change $±G)
Distance to pass: $H remaining
```

### 2. Daily progression

One-line per session:

```
Mon: <grade> <score>% · N trades · $±X · <top violation>
Tue: ...
...
```

### 3. Streak progression

Read `rule_compliance.csv`. Render:
```
Streak entering week: N · Streak exiting week: M
Streak changes: <continued|broken on <date>|rebuilt starting <date>>
```

### 4. 10-rule heatmap

Which rules failed MOST this week? (Aggregate across sessions.)

```
Most-broken rules this week:
  R1 size_locked:   3/5 sessions
  R3 daily_cap:     2/5 sessions
  R5 no_revenge:    2/5 sessions
Most-kept rules:
  R7 loss_limit:    5/5 sessions ✓
  R10 profit_stop:  5/5 sessions ✓
```

### 5. Regime / slice distribution

Bucket trades by:

- **Day-of-week**: where's the money?
- **Hour-of-day**: where's the money?
- **Hold duration**: quick / patient / long — which slice won and lost?
- **Contract size**: which size netted most?
- **Session grade**: A/B vs C-or-worse contribution to week P&L

Render the table where the structural pattern is clearest (usually hold-duration or session-grade).

### 6. Best day / worst day breakdown

Identify the week's best and worst sessions. For each, explain in 2-3 lines what was different. Common factors:

- Few trades vs many
- Patient holds vs quick scalps
- Clean rule compliance vs multiple violations
- Time of first trade

### 7. Commission audit

```
Week commissions: $X
% of gross P&L: Y%
If you'd cut sub-6-tick trades: additional $Z in net
```

### 8. The structural lesson

ONE observation about HOW Jaime traded (not what markets did). Examples:

- "The Thu/Fri pattern (not-first-trade, 9:41-11:00, held through pullback) produced 100% of this week's net profit. Mon-Wed was pattern-broken scalping."
- "Sub-6-tick scalps made up 40% of trades but -120% of P&L contribution. Eliminating them alone adds $X to next week."
- "Size was stable through Wed, escalated Thu/Fri — but those were the winners. Size discipline might matter less than setup discipline."

Prefer observations that Jaime can act on NEXT Monday.

### 9. Next week's commitment

Derived from the structural lesson:

- One commitment (not five)
- Testable within a week
- Behavioral, not strategic

```
Next week's commitment: <one sentence>
Measurable target: <specific metric that improves>
Ceiling check: <hard rule, e.g. "hard stop at 10 trades per session">
```

### 10. Follow-up

```
Consider:
  - /insight-capture the structural lesson to Daily/<date>.md
  - /accountability-post the commitment
  - Run `python ~/mes-intel/tools/review_rotation.py` for this week's themed deep-dive
```

## Output format

Top-line → daily progression → streak → rule heatmap → slice distribution → best/worst → commission → structural lesson → commitment → follow-up.

Target length: 60-100 lines.

## Don'ts

- Don't generate "coaching" prose. State observations, leave the call.
- Don't make the structural lesson a vague platitude ("be more patient"). Be specific ("entries under 6 min have negative expectancy, commit to 6-min thesis pre-click").
- Don't recommend strategy changes — meta-learning is for the MES app's MetaLearner, not this skill.
- Don't include PERSONAL trades.
