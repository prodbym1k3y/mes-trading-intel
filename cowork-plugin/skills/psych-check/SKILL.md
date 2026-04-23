---
name: psych-check
description: Real-time emotional/state assessment before, during, or after a session. Detects revenge-timing, overtrading, hole-chasing, quick-grab, and long-bias triggers from the live journal state and returns a traffic-light verdict plus a specific intervention. Trigger phrases include "psych check", "am I tilted", "should I keep trading", "state check", "am I good to trade", "check my head".
---

# Psych Check — State Assessment

Before anything else: load `brain-files`, read `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md` in full. That is the anchor for every verdict in this skill. Also load `mes-context` for instrument specs.

This skill is invoked when the trader wants to know whether to take the next trade, keep trading, or stop. It is the most-used live skill. Be fast, specific, and physical (prescribe movement, not just thought).

## Required inputs

Usually none — this skill reads from the live state in `mes-brain` (the SQLite DB). If the user types `/psych-check` with no args, run the full assessment below. If they describe a feeling ("I'm pissed", "I just gave back $80"), use that as extra signal.

## Workflow

### 1. Pull live session state

From `mes-brain`, for the active Phoenix date (06:30 today → now):

- Trade count today
- Cumulative P&L today (in $ and ticks)
- Last 3 trade outcomes (W/L), and time of last close
- Current streak (consecutive Ws or Ls)
- Time of day (Phoenix)
- Average hold time today vs baseline
- If a position is currently open: time in trade, unrealized P&L, entry vs current

### 2. Scan the five triggers

For each of the five from the psychology profile, compute a boolean + severity:

| Trigger | Rule | Severity |
|---|---|---|
| **REVENGE** | seconds_since_last_loss < 300 AND last_trade was L | 🔴 hard block |
| **OVERTRADE** | trade_count ≥ 10 | 🔴 hard stop. ≥ 8 is 🟡 |
| **HOLE** | session_pnl ≤ -$50 | 🟡 size-down. ≤ -$75 is 🔴 stop |
| **QUICK-GRAB** | avg_winner_hold_today < 3 min AND last trade was an early exit | 🟡 awareness |
| **LONG-BIAS** | long_share_today > 75% | 🟡 awareness |

Plus one positive flag:
- **EDGE WINDOW**: is the clock inside 08:00–09:00 Phoenix? (Hour 08 is 100% WR historically.) If so, note it.

### 3. Render the verdict

Output exactly this structure:

```
STATE: [🟢 GREEN | 🟡 YELLOW | 🔴 RED]
Phoenix time: HH:MM  |  Session: N trades, $±X, last 3: W/L/W  |  Streak: ±N

TRIGGERS FIRING:
  [⚠️ TRIGGER_NAME] — <one-line why, with the number>
  ...

[If GREEN:]
Cleared to trade. The pattern that's working today: <name the edge>.
Protect it: <one specific rule, e.g. "keep size at 2, hold 3+ min">.

[If YELLOW:]
Warning state. Take the next trade only if ALL of these are true:
  1. <specific condition from the profile>
  2. <specific condition>
  3. <specific condition>
If not, <specific pause>.

[If RED:]
Stand down. Close the DOM.
Reason: <name the specific trigger with the stat>
Intervention: <specific physical action with a timer>
Re-check: <when to run /psych-check again>
```

### 4. The intervention must be physical

Not "take a breath." Actual actions, each with a concrete timer:

- "Stand up. Walk to water. Drink 8oz. Sit back down. 5-minute timer starts now."
- "Close the DOM. Walk outside. 15-minute timer. Come back and run /psych-check before clicking anything."
- "You are done. Close the platform. Open tomorrow's /morning-prep at 05:50 Phoenix."

### 5. Never

- Never say "you got this" or "shake it off." The profile is explicit: this is **state regulation, not willpower.**
- Never over-explain. The trader is on the clock. Verdict in the first line; reasoning below.
- Never recommend a specific trade direction. This skill is about *whether* to trade, not *what* to trade.
- Never soften a RED. If revenge or deep-hole triggers are firing, the answer is stop. Period.

## Output length

Hard cap: 25 lines of markdown. This is a glance-and-go skill.

## When to use proactively

If any other live skill (pre-trade-checklist, grade-trade, regime-check) detects a triggered state, it should silently run psych-check's logic and surface the verdict at the top of its own output. Treat psych-check as the universal state-guard.

---
*Anchors on: `Brain/obsidian/Obsidian Vault/Learnings/psychology-profile.md`*
