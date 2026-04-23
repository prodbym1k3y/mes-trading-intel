---
name: grade-day
description: End-of-day Apex session review — pulls today's trades from journal/all_trades.csv, scores against the 10 Apex rules, classifies the psychological state that drove the session (Patient vs Triggered Jaime), narrates the session arc (Open / Build / Edge Window / Fatigue), names which leak/edge slices fired, and surfaces ONE behavioral commitment for tomorrow. Mirrors `grade_session.py` + `leak_detector.py`. Trigger phrases include "grade my day", "EOD", "session review", "how did I do today", "grade today", "wrap the session".
---

# Grade Day — End-of-Session Apex Review (Performance + Behavioral)

This is the daily reflection ritual. Goal: not to feel good or bad, but to surface ONE concrete behavioral adjustment for tomorrow. Every session is also a data point for the psychological profile — this skill writes that data.

## Required context

1. Load `mes-context` first.
2. Use `brain-files` to read `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` — the behavioral anchor for state classification.
3. Default account: **APEX-565990-01**. Authoritative version on PC: `python ~/mes-intel/tools/grade_session.py` followed by `python ~/mes-intel/tools/leak_detector.py`. This is the cowork mirror.

## Workflow

### 1. Pull today's APEX trades

Read `journal/all_trades.csv` via `brain-files`, filter to `account == 'APEX-565990-01'` AND `session_date == today` (Phoenix). Order chronologically. For each trade also derive: **seconds since last exit**, **session P&L at entry**, **Phoenix entry hour**.

If zero APEX trades: end with "No APEX trades today. State: flat. /streak status / /weekly-review / /morning-prep for tomorrow?"

### 2. Headline numbers

```
Date: <yyyy-mm-dd>
Trades: N · Win rate: X%
Gross: $A · Commissions: $B · Net: $C
Avg winner: $D · avg loser: $E · profit factor: <pf>
Worst trade: $F (#N, <ticks>t in <duration_sec>s)
Total contracts traded: <sum>
Max intraday DD: $G
```

### 3. Per-trade grade (5 axes)

For each trade, render: `[HH:MM] [L/S] [Nct] [+/-$X] [Setup/Entry/Mgmt/Exit/🧠 grades] [1-line note]`

| Axis | Measures |
|---|---|
| **Setup** | Edge slice or leak slice from current_edges/leaks? |
| **Entry** | Price near plan, or chase? Within proven 10-pt hold window? |
| **Mgmt** | Respected stop? Patient through pullback? |
| **Exit** | Captured available, or quick-grab / overstay? |
| **🧠 State** | Which "Jaime" — Patient, or Triggered (which trigger)? |

### 4. The 10-rule Apex grade

Score each rule:

| Rule | PASS/FAIL | Why |
|---|---|---|
| R1 size_locked | | All trades same size? Mid-session escalation? |
| R2 opening_cap | | ≤2 trades in first 30 min ET? |
| R3 daily_cap | | ≤10 trades total? |
| R4 stop_discipline | | Any loss > 4pts (5ct) / 5pts (7ct)? |
| R5 no_revenge | | Same-side re-entry within 5 min of a loss? |
| R6 edge_quality | | All trades ≥6-tick gross edge expectation? |
| R7 loss_limit | | Total daily loss < $300? |
| R8 dd_buffer | | Session ever closed < $48,500? |
| R9 no_tilt | | Any cluster of 3+ trades in <10 min after a loss? |
| R10 profit_stop | | If +$300 hit intraday, did he stop? |

Final score: % rules passed. Letter grade: A=90+, B=75-89, C=60-74, D=50-59, F=<50.

This format matches `journal/rule_compliance.csv` so it can be appended via `brain-files`.

### 5. The session arc (psychological)

Narrate using the phases from psychology-profile.md:

```
THE ARC
06:30–07:00 (Open)        : <state, count, trigger fires>
07:00–08:00 (Build)       : <state, count>
08:00–09:00 (EDGE WINDOW) : <did he trade his historical 100% WR hour?>
09:00–11:00 (Mid)         : <state drift?>
11:00–13:00 (Fatigue)     : <late-day discipline?>
```

Flag explicitly:
- **Pre-noon profit stop**: green before 09:00 Phoenix → did he stop, per rule?
- **Hour 06 exposure**: how many trades in the leak hour?
- **Hour 08 capture**: how many trades in the edge window?
- **The proven 10-pt hold prototype window** (09:41-11:00 ET = 06:41-08:00 Phoenix): any trades in it?

### 6. Commission audit

```
Round-trips: N · Commission paid: $X · % of gross: Y%
```

If Y > 50%: "Commission drag is the silent leak today."

### 7. Leak slices that fired

Read `claude-memory/project_current_leaks.md`. For each top-5 leak, count today's matching trades:

```
Today's leak exposure:
  - quick (<3m): 4 trades, –$87 contributed
  - short direction: 3 trades, –$45
  - 06h hour: 2 trades, –$22
  TOTAL leak: –$154 (X% of today's loss)
```

### 8. Edge slices that fired

Same against `project_current_edges.md`:

```
Today's edge capture:
  - patient (3-15m): 2 trades, +$120
  - long direction: 5 trades, +$80
  TOTAL edge: +$200
```

### 9. Which Jaime showed up today?

One-sentence classification:
- **"Mostly Patient"** — 0–2 triggered trades, ≤10 trades, state stable
- **"Drifted"** — started Patient, drifted Triggered mid-session. **Identify the inflection trade** (the one that broke the state)
- **"Triggered early"** — first 3 trades showed a trigger. Session likely unsalvageable from there
- **"Recovered"** — started Triggered, recovered to Patient. **Note what broke the spiral**

### 10. Pattern recognition (what repeated)

- All losses concentrated in one state? (e.g. "4 of 4 losses were Triggered-Revenge")
- All wins in one condition? (e.g. "all 3 winners were Trade #3+ patient hold")
- Trigger cascades — did one trigger spawn another?
- Entries during low-edge slices?

### 11. Comparison to baseline

Read last 5 sessions from `journal/all_trades.csv` and `rule_compliance.csv`. Compare today's net P&L, win rate, grade, **triggered-trade share**, **avg winner hold time** against rolling baseline. Today is "above / at / below baseline."

### 12. Tomorrow's commitment

ONE specific behavioral commitment derived from the data + state classification:

- If R5 failed AND today net negative → "Tomorrow: 90-second hard cool-down between trades. Phone timer."
- If R1 failed → "Tomorrow: lock size at 5 contracts. No mid-session changes regardless of conviction."
- If quick scalps top leak → "Tomorrow: every entry has a 6-min minimum hold thesis written before clicking."
- If today A/B + matched proven pattern → "Tomorrow: same rules, same size. Don't innovate. Protect the streak."
- If today D/F → "Tomorrow: 5-trade max. After 2 losses, walk."
- If "Drifted" classification → "Tomorrow: at the inflection-trigger moment (e.g. first revenge candidate), close the DOM and run /psych-check."
- If "Triggered early" → "Tomorrow: skip the first 30 minutes. Let Hour 08 be the start of trading."

ONE commitment. Not five.

### 13. Suggested follow-ups

```
- Append today's row to rule_compliance.csv (via brain-files write_file)
- /insight-capture the inflection moment if "Drifted" today
- /accountability-post the commitment
- /streak — see where the streak stands
- /identify-leak — if commissions ate >50% of gross
```

## Output format

Headline → per-trade table → 10-rule grade → session arc → commissions → leaks fired → edges fired → Which Jaime → patterns → vs baseline → commitment → follow-ups. 60-90 lines.

## Don'ts

- Don't grade emotionally. Grade against rules + state.
- Don't suggest >1 commitment for tomorrow.
- Don't include PERSONAL trades. APEX-565990-01 only.
- Don't speculate on market direction or "should have done X" — only behavioral analysis.
- Don't soften the "Triggered" diagnosis. The whole point is naming it lets him fix it.
