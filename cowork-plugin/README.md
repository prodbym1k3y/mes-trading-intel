# mes-trading-intel (v0.2.1)

Cowork integration for Jaime's MES futures trading — specifically the **APEX-565990-01 50k evaluation**. Wires the shared Brain (SQLite + journal CSVs + Obsidian vault + Claude memory) into Claude Cowork with 17 skills built around the two non-negotiable rules:

1. **Never mix PERSONAL and APEX accounts.** Every skill defaults to APEX-565990-01.
2. **This is a feedback + coaching system, NOT signal generation.** No skill recommends entries.

The Brain is the unifying layer — Mac and PC both sync it via iCloud, the PC's `~/mes-intel/tools/` CLI writes data into it, Obsidian holds long-form notes, Claude memory holds durable facts. This plugin gives Cowork direct access to all of it.

## What's inside

### Skills (16 total)

#### Daily flow
| Slash command | When to use | PC tool equivalent |
|---|---|---|
| `/morning-prep` | Before the open — yesterday's grade, current edges/leaks, commitment for today | `morning.py` |
| `/psych-check` | **Live state guard** — am I Patient or Triggered? Should I take the next trade, keep going, or stop? | — |
| `/pre-trade-checklist` | Right before pulling the trigger — friction, refuses <6-tick edge | `pretrade.py` |
| `/grade-trade` | Right after closing — single-trade surgical review with state classification, optional runner log | `grade_trade.py` / `apex_trade_log.py` |
| `/grade-day` | After the close — 10-rule score + session arc + Which Jaime, tomorrow's commitment | `grade_session.py` + `leak_detector.py` |
| `/regime-check` | Postmortem only — market structure context for a specific trade | `market_context.py` + `gamma_analysis.py` |

#### Account + discipline
| Slash command | When to use | PC tool equivalent |
|---|---|---|
| `/prop-firm-status` | Live APEX balance, distance to bust/pass, posture verdict | `drawdown_monitor.py` |
| `/streak` | Discipline streak + pass-at-A/B-pace projection | `streak.py` |
| `/commission-check` | Size stress-test, break-even tick math, sub-6-tick leak audit | `commission_filter.py` |

#### Pattern + memory
| Slash command | When to use |
|---|---|
| `/playbook-match` | "Have I traded this setup before?" — historical analogs + prototype similarity |
| `/recall` | Search the whole brain for prior thinking on a topic |
| `/insight-capture` | Save a lesson/observation to Obsidian + brain-log.md + optionally claude-memory |
| `/accountability-post` | Paste-ready daily snippet for Discord/chat/email |

#### Periodic + system
| Slash command | When to use | PC tool equivalent |
|---|---|---|
| `/weekly-review` | Sat/Sun weekend retrospective — structural observations + next week's commitment | `review_rotation.py` |
| `/identify-leak` | When P&L feels off — top 3-5 systematic losers ranked by $, with fix per leak | `leak_detector.py` |
| `/sync-check` | Switching machines or troubleshooting — Brain + journal + lockfile + DB freshness | — |

#### Reference (auto-loaded)
| `/mes-context` | Ground truth for every other skill — accounts, rules, philosophy, data schemas |

### MCP servers

| Name | Backs | Powers |
|---|---|---|
| `mes-brain` | SQLite at `Brain/mes-state/mes_intel.db` (26 tables, mostly dormant; `learning_history` has 209 entries) | Legacy structured queries |
| `brain-files` | Filesystem at `Brain/` (= journal CSVs + Obsidian + memory + all PC-synced data) | Trade data reads, Obsidian writes, memory access |
| `mes-repo` | Filesystem at the trading repo ($MES_REPO_ROOT) | Reading plugin source / code |

**Note:** The real trade data is in `Brain/journal/all_trades.csv`, `apex_grade_log.csv`, `rule_compliance.csv`, NOT in `mes_intel.db`. Skills read from the CSVs via `brain-files`. The SQLite is present for legacy / pattern / learning-history queries.

## The non-negotiable rules

These are codified in `skills/mes-context/SKILL.md` and must hold across every skill:

### Rule 1: Never mix accounts

Every skill defaults to `account == 'APEX-565990-01'`. To query PERSONAL, user must explicitly say so. Combined analyses are labeled COMBINED.

### Rule 2: This is not a signal app

No skill says "take this trade" or "direction bias is long." Skills describe structure, grade decisions, surface slices — the user owns the call.

### Rule 3: Defer to PC tools where they overlap

When a skill overlaps with a PC CLI tool (e.g. `/grade-day` ↔ `grade_session.py`), the skill references the tool by name so Jaime knows the authoritative version exists. The cowork skill is the mobile / cross-platform mirror.

## Current APEX state (snapshot 2026-04-17)

- Balance: $49,948
- DD bust: $48,000 (trailing $2,000)
- Pass target: +$3,000 net from current = $53,000
- Week 1 result: 41 trades, gross +$213, commissions -$262, NET -$49
- Proven 10-pt hold pattern: Thu 4/16 + Fri 4/17 (not-first-trade, 9:41-11:00 ET, ~8m hold, low session count)

Skills pull fresh data on each call — the above is only the snapshot at install time. Current numbers come from querying `Brain/journal/all_trades.csv` live.

## Setup

### 1. Install

Drop `mes-trading-intel.plugin` into Cowork. Skills register automatically. Enable the plugin if it's not already.

### 2. Install runtimes

```sh
# Mac
brew install uv node

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
winget install OpenJS.NodeJS.LTS
```

### 3. Set env vars

**Mac** (`~/.zshrc` AND `launchctl` for GUI apps):

```sh
# ~/.zshrc
export BRAIN_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Brain"
export MES_BRAIN_DB_PATH="$BRAIN_ROOT/mes-state/mes_intel.db"
export MES_REPO_ROOT="$HOME/trading"

# Then for GUI (Cowork, etc.) — persists until reboot:
launchctl setenv BRAIN_ROOT "$BRAIN_ROOT"
launchctl setenv MES_BRAIN_DB_PATH "$MES_BRAIN_DB_PATH"
launchctl setenv MES_REPO_ROOT "$MES_REPO_ROOT"
launchctl setenv PATH "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
```

**Windows** (PowerShell, one-time persistent):

```powershell
[Environment]::SetEnvironmentVariable("BRAIN_ROOT", "$env:USERPROFILE\iCloudDrive\Brain", "User")
[Environment]::SetEnvironmentVariable("MES_BRAIN_DB_PATH", "$env:USERPROFILE\iCloudDrive\Brain\mes-state\mes_intel.db", "User")
[Environment]::SetEnvironmentVariable("MES_REPO_ROOT", "C:\trading", "User")
```

### 4. Restart Cowork (fully quit, reopen)

### 5. Verify

Type `/sync-check`. Should go green across Brain folder, lockfile, DB, Obsidian, memory, and journal CSVs.

## Troubleshooting

**`brain-files` sandboxed to wrong dir** — the filesystem MCP scopes at launch. If env vars weren't set when Cowork started, it defaults to cwd. Re-set env via launchctl/setx and fully restart Cowork.

**`mes-brain` returns empty tables** — the SQLite DB is the legacy app's brain. Most tables are empty until/unless the MES PySide6 app runs. Real data lives in `Brain/journal/` CSVs — check `/sync-check` output for CSV presence.

**Obsidian writes don't appear on the other machine** — iCloud sync delay (seconds-minutes). Check the iCloud icon in menu bar / system tray.

**Cowork chat mode (not Code mode) can't see MCPs** — by design. Cowork's autonomous chat mode only accepts remote MCPs (HTTPS/SSE). Local stdio MCPs (our `uvx`/`npx` ones) only work in Code mode. Use Code mode for MES analysis.

**Plugin version mismatch** — if you installed an older version, delete it from Customize → Personal plugins and drag the new `.plugin` file in fresh.

## Source

Plugin source lives in the trading repo at `cowork-plugin/`. To re-zip after edits:

```sh
cd /Users/m1k3y/trading/cowork-plugin
zip -rq /tmp/mes-trading-intel.plugin . -x "*.DS_Store"
cp /tmp/mes-trading-intel.plugin ~/Downloads/
```

Commit + push normally.
