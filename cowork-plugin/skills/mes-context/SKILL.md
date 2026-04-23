---
name: mes-context
description: Loads the canonical MES trading system context for Jaime — two-account rule (PERSONAL vs APEX-565990-01), system philosophy (feedback NOT signals), data sources (journal CSVs + brain DB), the PC tools at ~/mes-intel/tools/, current edges and leaks. Auto-load this skill first when any other MES skill is invoked, or when the user asks anything about their trading, the eval, the system, or the brain.
---

# MES Trading Intel — Ground Truth Context for Jaime

This is reference knowledge that EVERY other skill in the `mes-trading-intel` plugin depends on. Load this first. Treat the rules below as inviolable.

## Trader

**Jaime** — full-time futures day trader on MES (Micro E-mini S&P 500). Trades through AMP Futures (Rithmic infrastructure). Charts on ATAS (paid, has real order flow) and ThinkorSwim. Phoenix, AZ time zone (UTC-7, no DST). Python beginner — prefers simple, readable code with comments.

## RULE #1 — Always separate the two accounts

There are **TWO** accounts. They MUST never be mixed in any analysis. Mixing them silently corrupts every Apex calculation.

| Account | Source | Period | Money type | Apex rules apply |
|---|---|---|---|---|
| **PERSONAL** | AMP/Rithmic, ATAS exports | pre-2026-04-13 | Real personal $ | NO |
| **APEX-565990-01** | Rithmic exports | 2026-04-13 onward | Funded eval | YES — drawdown, profit target, daily cap |

The `account` column in `journal/all_trades.csv` distinguishes them. **Default to APEX-565990-01 only** unless the user explicitly says "personal" or "both" or "lifetime."

When summarizing or saving learnings, ALWAYS tag which account the learning applies to. Never assert a "lifetime win rate" or "total P&L" combining both without clearly labeling it as combined.

## RULE #2 — This system is FEEDBACK + COACHING, not signal generation

Per Jaime's explicit pivot on 2026-04-17. Do NOT recommend "take this trade" or "don't take this trade." Real order flow lives in ATAS (paid). Real options data is in paid platforms. yfinance/free-data tools are POSTMORTEM context only.

What this skill plugin IS for:
- Grading decisions after the fact
- Surfacing patterns in his own behavior
- Calling out leaks in commission, sizing, time-of-day, direction
- Reinforcing the proven 10-pt hold pattern from Thu+Fri 4/16-4/17

What it is NOT for:
- Predicting direction
- Generating entry signals from free price data
- Suggesting specific trade ideas

When a skill is tempted to recommend an entry, instead reframe as: "the data shows X — your call."

## Apex 50k Eval — Live Status

**APEX-565990-01** is currently active. Monitor every session.

- Starting balance: $50,000
- Current balance: ~$49,948 (as of 2026-04-17 snapshot — query journal for current)
- **Trailing drawdown bust: $48,000** (trailing $2,000 from peak, NOT $2,500)
- **Pass target: +$3,000 net profit** = ~$3,052 from current balance
- Commission: **$1.34/RT/contract** via Rithmic routing
- Symbol: MESM6 (June 2026)
- Size: 5-7 contracts (was 1 pre-eval; escalation Mon → Fri week 1)

### Week 1 (Apr 13-17) result
- 41 trades, 5 sessions
- Gross +$213.75, commissions –$262.64, **NET –$48.89**
- Win rate 41.5%
- Worst intraweek DD: $49,425 (Wed close — 29% of $2k buffer used)

### The breakthrough pattern (proven Thu+Fri 4/16, 4/17)
Two consecutive ~10-pt holds. Shared conditions:
1. NOT first trade of the session — already green
2. ~8-minute holds through pullbacks
3. 9:41-11:00 ET window (NOT opening bell)
4. Few total trades on those days (3-4 vs 10-13)

This is the prototype to reinforce. Reference it whenever a skill needs to flag "is today on the path to that pattern."

## Current edges and leaks (auto-updated)

Fresh edges and leaks live at `claude-memory/project_current_edges.md` and `claude-memory/project_current_leaks.md`. Read these when grading or analyzing — they're updated every brain sync.

Snapshot from 2026-04-23:
- **Edges**: A_or_B sessions (+$597), patient holds 3-15m (+$593), trade #3 (+$372), 08h hour (+$370), long direction (+$346), Thursdays (+$279)
- **Leaks**: C-or-worse sessions (-$574), quick scalps <3m (-$497), Mondays (-$446), short direction (-$322), 06h hour (-$295), 5-contract size (-$231)

## Data sources — where the truth lives

The Brain (iCloud-synced between Mac and PC) has THREE relevant data layers:

| Path (under `Brain/`) | What | Use for |
|---|---|---|
| `journal/all_trades.csv` | Every trade, both accounts, with `account` column | All trade analysis (filter to APEX by default) |
| `journal/apex_grade_log.csv` | Manually-logged Apex runner trades with hold conditions, tape trend, confidence | Studying the 10-pt hold pattern |
| `journal/rule_compliance.csv` | Per-session 10-rule grading, A-F score | Streak / session quality / leak analysis |
| `journal/trades_YYYY-MM-DD.csv` | Daily exports per session | Spot-check single days |
| `journal/summary.json` | PERSONAL summary (Mar 24 - Apr 10) | Pre-eval baseline |
| `journal/trades.db` and `journal/live_ticks.db` | SQLite versions of trade data | Programmatic queries |
| `journal/brain-log.md` | Append-only running session learnings | Read for context, append after sessions |
| `mes-state/mes_intel.db` | Old PySide6 app's brain (26 tables, mostly empty) | Legacy — `learning_history` has 209 entries from Apr 4 |
| `obsidian/Obsidian Vault/` | Long-form notes, trade write-ups, system docs | Reading + write-back via /insight-capture |
| `claude-memory/` | Durable facts (this folder) | Read at start of sessions |

**Trade CSV schema** (`all_trades.csv` and daily files):
`trade_num, session_date, account, direction, contracts, entry_time, entry_price, exit_time, exit_price, duration_sec, gross_pnl, commission, pnl_dollars, ticks, cum_pnl`

**Apex runner log schema** (`apex_grade_log.csv`):
`logged_at, trade_date, trade_time_et, direction, contracts, entry_price, exit_price, ticks, hold_seconds, net_pnl, session_trade_num, session_pnl_entering, session_pnl_exiting, recent_loss_within_5min, held_through_pullback, time_bucket, tape_trend, confidence_1to5, notes`

**Rule compliance schema** (`rule_compliance.csv`):
`date, trades, net_pnl, score_pct, R1_size_locked, R2_opening_cap, R3_daily_cap, R4_stop_discipline, R5_no_revenge, R6_edge_quality, R7_loss_limit, R8_dd_buffer, R9_no_tilt, R10_profit_stop, violations`

The 10 rules are: size locked, opening cap, daily cap, stop discipline, no revenge, edge quality, loss limit, DD buffer, no tilt, profit stop. Each is PASS or FAIL per session.

## The PC tools at `~/mes-intel/tools/` (and `~/workflows/apex/`)

Jaime built a parallel CLI toolkit on his PC. Many of these skills should reference the matching tool when relevant — Jaime can run them directly in his terminal for the authoritative answer:

| Tool | Purpose |
|---|---|
| `performance_report.py --all` | Full 12-section analysis |
| `leak_detector.py` | Where money is going |
| `grade_session.py` | Daily 10-rule grade |
| `grade_trade.py` | Single-trade grading |
| `streak.py` | Discipline streak (threshold: score >=75%) |
| `drawdown_monitor.py` | Live APEX status |
| `apex_setup_detector.py` | Score conditions vs Thu/Fri prototype |
| `commission_filter.py` | Size stress-test |
| `pretrade.py` | Pre-entry friction CLI (refuses <6-tick edge) |
| `morning.py` | Pre-market sequence (import + monitor + detector + preflight) |
| `dashboard.py` | Unified view of all key snapshots |
| `apex_trade_log.py` | Runner trade journal append |
| `daily_post.py` | Generate accountability snippet, copy to clipboard |
| `sim_gate.py` | Track which new rules are proven (N clean sim trades) |
| `accountability_export.py` | Daily Discord/chat snippet |

Workflows at `~/workflows/apex/`:
`pre-market.md`, `live-monitor.md`, `log-runner.md`, `weekend-review.md`, `commission-stress-test.md`

When a skill in this plugin overlaps with one of these tools, **reference the tool by name** so Jaime knows the authoritative version exists.

## Instrument specs

| Spec | Value |
|---|---|
| Tick size | 0.25 points |
| Tick value | $1.25 |
| Point value | $5.00 |
| Commission | $1.34/RT/contract (Rithmic via AMP) |
| Symbol | MESM6 (June 2026) |
| Trading hours (RTH) | 6:30 AM – 2:00 PM Phoenix (9:30-17:00 ET) |
| Settlement | 1:00 PM Phoenix |
| Timezone | America/Phoenix (UTC-7, no DST) |

**Commission math reference:**
- $1.34 × 5 contracts = $6.70/RT
- $1.34 × 7 contracts = $9.38/RT
- Break-even on 5-contract trade = 1.34 points (5.4 ticks) gross before any move
- Sub-6-tick scalps are **mathematically negative expectancy** after fees

## Behavioral patterns to call out

The user is aware of these — surface them when relevant, never moralize:

- **Overtrading is the #1 issue** — sessions with ≤11 trades have been profitable, ≥13 have lost. Hard cap: 10 trades.
- **Tilt/revenge cluster** — after losses, he enters rapid-fire trades within 90 sec. (Mar 26: 22 trades in 17 min. Mar 31: 10 in 8 min.)
- **Opening-bell overtrading** — Mon 4/13 was 10 trades in first 34 minutes.
- **Stop discipline** — Trade 8 Mon 4/13 ran -8.25 pts on 5 contracts (-$206) before exit.
- **Size escalation mid-session** — went 1 → 5 → 7 contracts before proving the 10-pt hold.

## Don'ts (hard rules)

- Never combine PERSONAL and APEX in P&L without explicitly labeling combined.
- Never recommend a trade entry — the system is feedback only.
- Never assume current balance from old memory snapshots — query `journal/all_trades.csv` for `cum_pnl` against APEX-565990-01.
- Never override or contradict `feedback_account_separation.md` or `feedback_system_philosophy.md`.
- Don't tag Jaime's MEMORY.md with new entries unless the fact is durable and project-level.
