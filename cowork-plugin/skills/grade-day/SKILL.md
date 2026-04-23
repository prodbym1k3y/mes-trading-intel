---
name: grade-day
description: End-of-day quantitative + behavioral review. Grades each trade against the system's signals AND classifies the state that took it (Patient vs Triggered Jaime). Surfaces the session's psychological arc and names tomorrow's ONE adjustment. Trigger phrases include "grade my day", "EOD review", "how did I do today", "post market review", "wrap the day".
---

# Grade Day — End-of-Day Performance + Behavioral Review

This is the daily reflection ritual. The goal is not to make the trader feel good or bad — it is to surface **concrete patterns that compound into improvement**. Every session is a data point for the psychological profile; this skill writes it.

## Required context

1. Load `mes-context` — instrument specs, schema.
2. Load `brain-files` → read `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` in full. Every grade, every pattern, every adjustment is framed against this.
3. Use `mes-brain` MCP for queries. Today's Phoenix date is the session boundary unless the user specifies another.

## Workflow

### 1. Pull today's trades

Query `journal_trades` for entries in today's RTH window (06:30 AM – 13:00 PM Phoenix). Order chronologically. For each trade capture: entry time, exit time, side, size, instrument, entry price, exit price, P&L (in $ and points), AI grade if present, regime at entry, account, **seconds since last exit**, **session P&L at entry**.

If there were no trades, end here with: *"No trades today. State: flat. Want to run /morning-prep for tomorrow instead?"*

### 2. Headline numbers

```
Trades: N          Win rate: X%
Net P&L: $Y (Z points)
Avg winner: $A     Avg loser: $B
Profit factor: <gross_wins / gross_losses>
Max intraday DD: $C
By account: personal $X | apex $Y
```

### 3. Per-trade grade (5 axes, A/B/C/D/F each)

| Axis              | Measures                                                                       |
|-------------------|--------------------------------------------------------------------------------|
| **Setup**         | System aligned at entry? (regime + ensemble + zone)                            |
| **Entry**         | Price near the signal trigger, or chase?                                       |
| **Management**    | Respected invalidation? Trailed vs panic-exited?                               |
| **Exit**          | Took what was there, or cut too early / overstayed?                            |
| **🧠 State**      | Which of "Two Jaimes" took this trade? (Patient vs Triggered-[which trigger]) |

Render as a compact per-trade row:
`[HH:MM] [L/S] [size] [+/-$] [S/E/M/X/🧠 grades] [1-line note]`

### 4. The session arc

Narrate the psychological arc of the session using the phases from the profile:

```
THE ARC
06:30–07:00 (Open)        : <what happened, which state>
07:00–08:30 (Build)       : <what happened>
08:00–09:00 (Edge Window) : <did he trade his 100% WR hour?>
09:00–13:00 (Fatigue)     : <state drift?>
```

Flag explicitly:
- **Pre-noon profit stop**: if green before 09:00 Phoenix, did he stop? (Rule says he should.)
- **Hour 06 exposure**: how many trades in the 25%-WR leak hour?
- **Hour 08 capture**: how many trades in the 100%-WR edge window?

### 5. Pattern recognition (what repeated)

Look across today for:

- All losses concentrated in one state (e.g., "4 of 4 losses were Triggered-Revenge trades") → flag.
- All wins concentrated in one condition (e.g., "all 3 winners were Trade #3+ of session, patient hold") → flag.
- Trigger cascades: did a revenge spiral happen? Did overtrading start after a specific trade?
- Entries during low ensemble confidence or uncertain regime → flag.

### 6. Which Jaime showed up today?

One-sentence classification:
- **"Mostly Patient"** — 0–2 triggered trades, pace under 10, state stable.
- **"Drifted"** — started Patient, drifted Triggered mid-session. Identify the inflection trade.
- **"Triggered early"** — first 3 trades showed a trigger. Session likely unsalvageable from there.
- **"Recovered"** — started Triggered, recovered to Patient. Note what broke the spiral.

### 7. Vs baseline

Pull the last 20 RTH sessions. Compare today's: win rate, profit factor, triggered-trade share, avg hold on winners.
Today vs baseline: **above / at / below**. Say it plainly.

### 8. Tomorrow's ONE adjustment

End with **one specific behavioral adjustment** derived from today's patterns, framed against the psychology profile:

- "Skip the first 30 min. Today's 2 losses in Hour 06 = $X. Hour 06 is a documented leak."
- "Hard cap 8 trades tomorrow. Today you took 12; trades 9–12 were all Triggered."
- "After any loss tomorrow, stand up. Today you re-entered 3× within 90s; all lost."
- "Trail by structure, not by dollar. Today you cut 2 winners at +4t that went to +12t."

**ONE** adjustment. Not five. Compounding requires focus.

### 9. Write the session note

Save to `Brain/obsidian/Obsidian Vault/Daily/YYYY-MM-DD.md` in this frontmatter form:

```yaml
---
date: YYYY-MM-DD
trades: N
net_pnl: $X
grade: [A/B/C/D/F overall]
which_jaime: [mostly_patient | drifted | triggered_early | recovered]
top_trigger: [revenge | overtrade | hole | quick_grab | long_bias | none]
edge_captured: [true/false]  # did he trade the 08:00 window patient?
tomorrow_adjustment: <one line>
---
```

Body: the full grade-day output. This file becomes the training data for pattern refinement.

## Output format to the user

In order:
1. Headline numbers
2. Per-trade table
3. THE ARC
4. Which Jaime showed up
5. Patterns I noticed
6. Vs baseline
7. Tomorrow's ONE adjustment

End there. No pep talk.

## Don'ts

- **Don't grade emotionally.** No "you traded well!" A winning day from Triggered Jaime is luck, not edge. A losing day from Patient Jaime is cost of doing business.
- Don't recommend strategy changes — MetaLearner's job.
- Don't write a wall of text. Per-trade table is the meat; prose is the connective tissue.
- Don't suggest more than one adjustment.
- Don't skip the session note write — future grading depends on it.

---
*Anchors on: `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` | Writes: `Brain/obsidian/Obsidian Vault/Daily/`*
