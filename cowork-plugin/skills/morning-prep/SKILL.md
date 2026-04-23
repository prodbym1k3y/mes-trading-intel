---
name: morning-prep
description: Pre-market briefing for Jaime's Apex eval session. Pulls yesterday's grade, current edges/leaks, account state vs DD bust, the 10-pt hold pattern reminder, and a today-focused commitment. Mirrors the PC tool `morning.py` in cowork form. Trigger phrases include "morning prep", "premarket", "morning brief", "what should I watch today", "preflight", "start the morning".
---

# Morning Prep — Pre-Market Brief for the Apex Eval

This is the morning-of-trading ritual. It's a **feedback-and-commitment** brief, not a signal generator. Goal: Jaime sits down with a clear read on yesterday + a single concrete commitment for today.

## Required context

Always load `mes-context` first. Default account: **APEX-565990-01**.

The authoritative version of this routine on the PC is `python ~/mes-intel/tools/morning.py`. This skill is the cowork mirror — useful when Jaime's on the Mac or hasn't booted the PC yet. If he's at his PC, suggest running `morning.py` instead since it imports fresh Rithmic data.

Use the `mes-brain` MCP for SQL queries, `brain-files` for CSV reads (filter to `account == 'APEX-565990-01'`).

## Workflow

### 1. Today's date + session context

Phoenix date and ET session window. Note day of week (Monday is a known leak — flag it).

### 2. Account state — APEX-565990-01

Read `journal/all_trades.csv`, filter to `APEX-565990-01`. Compute:

```
Current cum_pnl: $X (last cum_pnl value)
Current balance: $50,000 + cum_pnl = $Y
Distance to DD bust ($48,000): $Z
Distance to pass (+$3,000 net): $W remaining
Trades logged this week: N
Best session this week: $A (date)
Worst session this week: $B (date)
```

If balance is within $500 of $48,000: **flag as RED, recommend smallest-size sim day or no trade**.

### 3. Yesterday's grade

Read `journal/rule_compliance.csv`. Get the most recent row (yesterday's session if it exists). Render:

```
Yesterday: <date> · grade <A/B/C/D/F> (score_pct%)
Trades: N · Net: $X
Rules failed: <comma-separated list>
```

If yesterday was a C-or-worse session (a known leak): say so plainly. The rule is "if you only traded A/B sessions, you pass in ~11-12 sessions."

### 4. Current edges and leaks

Read the live files at `claude-memory/project_current_edges.md` and `claude-memory/project_current_leaks.md`. Render the top 3 of each as a tight table:

```
TODAY'S TAILWINDS (do more of):
  - <slice>: NET +$X over N trades
  - ...

TODAY'S TRAPS (do less of):
  - <slice>: NET -$X over N trades
  - ...
```

If today's day-of-week or hour-of-day is in the LEAKS list, call it out: "Today is <Monday>, your worst day historically (-$446). Tighter rules required."

### 5. The 10-pt hold reminder

Always include this block (the prototype Jaime is reinforcing):

```
THE PROVEN PATTERN (Thu/Fri 4/16-4/17):
  - NOT first trade — already green
  - 9:41-11:00 ET window
  - ~8 minute hold through pullback
  - Few trades that day (3-4 total)
```

### 6. Streak status

Read `journal/rule_compliance.csv`. Count consecutive sessions with score_pct >= 75. Render:

```
Discipline streak: N sessions
Last break: <date> (score_pct%)
```

### 7. Today's commitment

End with a single one-line commitment Jaime types/confirms before opening positions. Generate based on what surfaced:

- If yesterday was D/F → "Today: max 5 trades. Hard stop after 2 consecutive losses."
- If yesterday was A/B → "Today: protect the streak — same rules, no size escalation."
- If Monday → "Today is Monday (-$446 leak). One trade before 09:00 ET; full stop if down 1R by 09:30."
- If close to pass target (within $500) → "Within $500 of pass. Lock size, only A-grade setups, walk after first +1R."
- If close to DD bust → "Within $500 of bust. Sim-only mode today."

### 8. Suggested PC tool to run next

Reference the tool that gives the authoritative answer:

```
For full version with fresh Rithmic import + gamma levels, run:
  python ~/mes-intel/tools/morning.py
```

## Output format

In order: **Account state**, **Yesterday's grade**, **Tailwinds + Traps**, **Proven pattern reminder**, **Streak**, **Today's commitment**, **PC tool suggestion**.

Length: under 50 lines. Read-in-60-seconds brief.

## Don'ts

- Don't predict direction or recommend entry direction.
- Don't reference market regime / signal ensemble — those are old-app concepts not used in the current system.
- Don't mix PERSONAL into anything here. APEX-565990-01 only.
- Don't include "trading horoscope" — VIX/GEX/news. The PC's `gamma_analysis.py` handles that as POSTMORTEM context, not morning signal.
