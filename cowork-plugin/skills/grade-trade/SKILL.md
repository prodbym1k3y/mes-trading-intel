---
name: grade-trade
description: Surgical grade of a single Apex trade — combines trade reconstruction (from journal/all_trades.csv), proven 10-pt hold prototype match, edge/leak slice tagging, AND psychological state classification (Patient vs Triggered Jaime, with named trigger). Optionally appends to apex_grade_log.csv as a runner entry. Mirrors `grade_trade.py` + `apex_trade_log.py`. Trigger phrases include "grade this trade", "review this trade", "log a runner", "was that a good trade", "what was that trade".
---

# Grade Trade — Single-Trade Postmortem (Performance + State)

For when Jaime just closed a trade — usually a runner — and wants the system's read on whether it fit the proven pattern, what slice it belongs to, what state took it, and what to take from it.

## Required context

1. Load `mes-context` first.
2. Use `brain-files` to read `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` — the behavioral anchor.
3. Default account: **APEX-565990-01**. Authoritative version on PC: `python ~/mes-intel/tools/grade_trade.py` and `apex_trade_log.py` for runner journaling.

## Inputs to gather

If user didn't already say:
1. **trade_num** OR **entry_time** to identify the trade in `journal/all_trades.csv`
2. If trade isn't in journal yet: side, contracts, entry_price, exit_price (or "still open"), entry_time

If multiple matches in journal, ask which one.

## Workflow

### 1. Reconstruct the trade

Pull from `journal/all_trades.csv`. Display:

```
Trade #N · <yyyy-mm-dd> <hh:mm> ET · APEX-565990-01
<Direction> <contracts>ct · entry $X.XX → exit $Y.YY · <ticks>t · held <Hms>
Gross: $±X · Commission: $Z · Net: $±W

Session context at entry:
  Trade #M of session
  Session P&L entering: $±A · exiting: $±B
  Seconds since last exit: T
  Phoenix hour at entry: H
  ET window: <0930_1030 | 1030_1130 | ...>
```

### 2. Match against the proven 10-pt hold prototype

The Thu+Fri 4/16-4/17 prototype:
- NOT first trade of the session
- 9:41-11:00 ET window (06:41-08:00 Phoenix)
- ~8 minute hold
- Fewer total trades that day (3-4)

```
Prototype fit:
  ✓/✗ Not first trade (was #M of session)
  ✓/✗ In 9:41-11:00 ET window
  ✓/✗ Held ~8 min through pullback (actual: <duration>)
  ✓/✗ Day's total trade count (today N vs prototype 3-4)

PROTOTYPE MATCH: <full | partial | none>
```

### 3. Edge / leak slice classification

Cross-reference against `claude-memory/project_current_edges.md` and `project_current_leaks.md`:

```
Edges this trade belongs to:
  - <slice>: NET +$X lifetime
  - ...

Leaks this trade belongs to:
  - <slice>: NET -$X lifetime
  - ...
```

### 4. Name the state that took the trade

Classify the entry using the psychology-profile vocabulary:

- **Patient Jaime** — No triggers firing, within edge window or proven prototype window, size matched session lock. Positive-EV decision regardless of outcome.
- **Triggered Jaime — Revenge** — Entered within 5 min of a loss. WR on this state historically: 26-29%. Even if it won, it was a negative-EV decision.
- **Triggered Jaime — Overtrade** — Session trade count ≥ 10 at entry. Selectivity already blown.
- **Triggered Jaime — Hole-chase** — Session P&L ≤ -$50 at entry. Entered to get back, not from edge.
- **Triggered Jaime — Quick-grab** (exit-side) — Unrealized gain exited before structure target. Applies to exit grading.
- **Triggered Jaime — Long-bias default** — Long trade with no strong setup, in a session where >75% of trades were long.
- **Triggered Jaime — Size escalation** — Contracts > session-lock size.

If multiple, list all.

### 5. The grade (6 dimensions)

| Dimension | Letter | Why |
|---|---|---|
| Setup quality | A-F | Edge slice or leak slice |
| Entry timing | A-F | Match proven window or chase? |
| Size discipline | A-F | Session-locked size kept? |
| Hold quality | A-F | Held through pullback per prototype? |
| Exit | A-F | Took available vs quick-grab / overstay |
| **🧠 State** | A-F | Patient = A; Triggered = D/F regardless of outcome |

Final letter grade.

### 6. Comparison to similar past trades — same state, not just same setup

Find 3-5 closest analogs in `apex_grade_log.csv` AND `journal/all_trades.csv` matching:
- Same direction
- Similar time bucket
- Similar size
- **Same state** (Patient vs Triggered) — this matters most

```
Closest historical analogs (same state):
  - 2026-04-16 09:41 long 5ct Patient: +$243 (8m07s)
  - 2026-04-15 09:12 short 5ct Patient: -$45 (2m, hit stop)

This trade vs same-state analogs: <better | similar | worse> by $X.

Same setup, DIFFERENT state (cautionary):
  - 2026-04-13 06:55 long 5ct Triggered-Revenge: -$87
```

Framing matters: a great setup taken in a Triggered state has a different base rate than the same setup taken Patient. The comparison must be state-matched.

### 7. The lesson (state-aware)

ONE forward-looking rule. Templates:

**If Patient Jaime took it (regardless of outcome):**
> "This is your A-setup — slice match + window + state. When all three align, this is your max-size trade type."

**If Triggered Jaime took it AND it lost:**
> "The setup was [valid/invalid], but the state was [trigger]. The decision-quality issue is [trigger], not the setup. Next intervention: [specific physical action from psychology-profile]."

**If Triggered Jaime took it AND it won:**
> "Won despite poor expectancy. This is variance. Don't recall it as a successful pattern — same state next time will lose by the base rate."

**If trade is still open:**
```
Status: open
Hold thesis: <still valid | degrading | invalidated>
Trail to: <specific price from structure, not dollar amount>
Invalidation: <specific price>
Psych check: <any trigger currently firing? If yes, /psych-check now.>
```

### 8. Offer to log as a runner

If the trade was held >5 min, identified direction, and net positive: offer to append to `apex_grade_log.csv` with:

```
Append this row?
  trade_date, trade_time_et, direction, contracts
  entry_price, exit_price, ticks, hold_seconds, net_pnl
  session_trade_num, session_pnl_entering, session_pnl_exiting
  recent_loss_within_5min: <yes/no>
  held_through_pullback: <yes/no>
  time_bucket: <0930_1030 | 1030_1130 | ...>
  tape_trend: <strong_trend | choppy | reversing — ask user>
  confidence_1to5: <ask user>
  notes: <ask user for one line>
```

If yes, append via `brain-files` MCP `write_file`.

## Output format

Trade summary → prototype fit → slice classification → state classification → grade table → analogs → lesson → optional log append. 50-70 lines.

## Don'ts

- Don't grade the OUTCOME — grade the DECISION. A losing trade with great setup + Patient state is a different problem than a winning trade with Triggered state.
- Don't compare to all-trade analogs without state filtering. Same-state base rates are the right denominator.
- Don't lecture if user invokes mid-trade — keep open-trade mode tactical.
- Don't claim historical analogs with N < 3.
- Don't soften a Triggered diagnosis with "but you got the trade right." Naming the state is what lets him fix it.
