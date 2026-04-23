---
name: streak
description: Discipline streak status for the Apex eval. Counts consecutive sessions with rule-compliance score >= 75% and projects pass-at-A/B-pace. Mirrors `streak.py`. Trigger phrases include "streak status", "discipline streak", "how's my streak", "am I on track", "A/B streak".
---

# Streak — Discipline Streak + Pass Projection

The single most motivating metric for Jaime: how many sessions in a row he kept the rules, and how fast he passes the eval if he ONLY trades A/B sessions.

## Required context

Always load `mes-context` first. Account: **APEX-565990-01**. Authoritative version: `python ~/mes-intel/tools/streak.py`.

## Workflow

### 1. Read rule_compliance.csv

Read `journal/rule_compliance.csv`. Order by date DESC.

Streak rules:
- A session counts in the streak if `score_pct >= 75`
- A session breaks the streak if `score_pct < 75`
- Skipped/no-trade days don't count or break

### 2. Compute streak

```
Current streak: N sessions (since <date>)
Longest streak ever: M sessions (<date> to <date>)
Last break: <date> (score_pct%)
```

### 3. A/B-only pass projection

Filter `rule_compliance.csv` to sessions with score_pct >= 75 (A and B grades). Compute:

```
A/B sessions to date: N (out of total M sessions)
Avg net P&L on A/B sessions: $X
```

Then read `journal/all_trades.csv`, filter to A/B sessions only (join by date), confirm avg P&L.

Pass projection at A/B-only pace:
```
Sessions remaining to pass: ceil($3,000_remaining / $avg_AB_session)
At 5 sessions/week: ~N weeks
```

### 4. C-or-worse audit

Read C/D/F sessions:
```
C-or-worse sessions: N (Y% of total)
Cumulative cost: $-X
```

If avg A/B is $+$ and avg C-or-worse is $-$, render the comparison plainly:
```
The math: every A/B session contributes $+$X. Every C-or-worse session costs $X.
Eliminating C-or-worse alone closes ~Y% of your gap to pass.
```

### 5. Common rule failures

For sessions in the recent 10 with score < 75, count which rules failed most often:
```
Top broken rules (last 10 sessions):
  R1 size_locked:  N times
  R5 no_revenge:   N times
  R3 daily_cap:    N times
```

### 6. The verdict

```
Streak: <length>
Pass-at-A/B-pace: <weeks>
Top fix to extend streak: <single rule that's most often broken>
```

## Output format

12-20 line block. Streak number prominent at top.

## Don'ts

- Don't reset / count down — only count up.
- Don't include PERSONAL trades.
- Don't moralize about the streak. State the math, the user owns the response.
