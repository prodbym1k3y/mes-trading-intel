# mes-trading-intel

Cowork integration for the [MES Trading Intelligence System](https://github.com/prodbym1k3y/mes-trading-intel). Wires the shared Brain (SQLite DB + Obsidian vault + Claude memory) into Claude Cowork via 3 MCPs and 12 trader-tuned skills, plus 1 reference skill.

The Brain is the unifying piece: both the Mac and PC sync it via iCloud, the desktop app reads/writes the SQLite, Obsidian holds long-form notes, and Claude memory holds durable facts. This plugin gives Cowork direct access to all of it.

## What's inside

### Skills

#### Daily workflow

| Slash command           | When to use                                                                  |
|-------------------------|------------------------------------------------------------------------------|
| `/morning-prep`         | Before the 6:30 AM Phoenix open — overnight, regime, levels, accounts        |
| `/pre-trade-checklist`  | Right before pulling the trigger — 5-axis safety check                       |
| `/regime-check`         | Mid-session — 5-second snapshot of regime + ensemble + dark pool + zones     |
| `/grade-trade`          | Right after closing a trade — surgical review with system reconstruction     |
| `/grade-day`            | After the close — day grade, per-trade table, tomorrow's adjustment          |

#### Pattern + memory

| Slash command           | When to use                                                                  |
|-------------------------|------------------------------------------------------------------------------|
| `/playbook-match`       | "Have I traded this before?" — historical analogs, win rate, regime fit      |
| `/recall`               | Search Obsidian + Claude memory + journal for prior thinking on a topic      |
| `/insight-capture`      | Save a trading insight, lesson, or rule update to the Brain                  |

#### Periodic

| Slash command           | When to use                                                                  |
|-------------------------|------------------------------------------------------------------------------|
| `/weekly-review`        | Sunday/Saturday — bigger-picture retrospective beyond daily grading          |
| `/identify-leak`        | Monthly or whenever P&L feels off — find systematic losers, ranked by cost   |

#### Account + system health

| Slash command           | When to use                                                                  |
|-------------------------|------------------------------------------------------------------------------|
| `/prop-firm-status`     | Before any session — drawdown, daily loss, profit target across all accounts |
| `/sync-check`           | When switching machines or troubleshooting — Brain + lockfile + DB freshness |

#### Reference (auto-loaded)

| `/mes-context`          | Ground-truth facts for every other skill — instrument specs, schema, agents  |

### MCP servers

| Name           | Backs                                  | Powers                                                          |
|----------------|----------------------------------------|-----------------------------------------------------------------|
| `mes-brain`    | SQLite DB at `Brain/mes-state/`        | All structured queries — trades, regimes, agents, learning      |
| `brain-files`  | Filesystem at `Brain/`                 | Obsidian vault, Claude memory, config files                     |
| `mes-repo`     | Filesystem at the trading repo         | Reading code, strategy implementations, agent source            |

## Setup

### 1. Install the plugin

Drop the `.plugin` file into Cowork. Skills register automatically.

### 2. Install runtimes (one time per machine)

The MCPs use `uvx` (Python tool runner) and `npx` (Node tool runner). Install both:

**Mac:**

```sh
brew install uv node
```

**Windows:**

```powershell
# uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# node — install from https://nodejs.org or:
winget install OpenJS.NodeJS.LTS
```

### 3. Set the three env vars

Each machine needs `MES_BRAIN_DB_PATH`, `BRAIN_ROOT`, and `MES_REPO_ROOT` so the MCPs find the right paths.

**Mac** (add to `~/.zshrc`):

```sh
export BRAIN_ROOT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Brain"
export MES_BRAIN_DB_PATH="$BRAIN_ROOT/mes-state/mes_intel.db"
export MES_REPO_ROOT="$HOME/trading"
```

Reload: `source ~/.zshrc` (or open a new terminal).

**Windows** (PowerShell, run once — persists across sessions):

```powershell
[Environment]::SetEnvironmentVariable("BRAIN_ROOT", "$env:USERPROFILE\iCloudDrive\Brain", "User")
[Environment]::SetEnvironmentVariable("MES_BRAIN_DB_PATH", "$env:USERPROFILE\iCloudDrive\Brain\mes-state\mes_intel.db", "User")
[Environment]::SetEnvironmentVariable("MES_REPO_ROOT", "C:\trading", "User")
```

Restart Cowork after setting these so the new env propagates.

### 4. Verify

In Cowork, type `/sync-check`. If everything wired correctly, you'll see green status across the board. Common failures:

- **`mes-brain` won't start**: `uvx` not on PATH, or `MES_BRAIN_DB_PATH` not set / file doesn't exist.
- **`brain-files` empty**: iCloud Drive hasn't synced the Brain folder to this machine yet. Wait, or right-click → Always keep on this device.
- **`mes-repo` won't start**: `npx` not on PATH (install Node), or `MES_REPO_ROOT` not set.

## Architecture

```
                    Cowork (Mac or PC)
                           │
            ┌──────────────┼──────────────┐
            │              │              │
      [mes-brain]    [brain-files]    [mes-repo]
       SQLite          Files            Files
            │              │              │
            ▼              ▼              ▼
   Brain/mes-state/   Brain/         $MES_REPO_ROOT
     mes_intel.db    (Obsidian +      (mes_intel/
                      memory)          source code)
            │
            ▼
        iCloud Drive
        (synced both ways
        Mac ↔ PC)
```

The MES desktop app on either machine writes to the brain DB (lockfile-guarded so only one writes at a time — see `/sync-check` and the lockfile rule in `mes_intel/main.py`). Cowork reads everything via MCP. Skills compose: most auto-load `mes-context` for instrument specs and schema, then query the right MCP for live state.

## Customizing

The skills hardcode some MES specifics (40% Value Area, Phoenix timezone, $1.25 tick value). To adapt to a different instrument or trader:

1. Edit `skills/mes-context/SKILL.md` — the ground-truth section.
2. Re-zip the plugin and re-install.

## Troubleshooting

**"MCP server failed to start"** — Verify the runtime and env var:
```sh
which uvx           # for mes-brain
which npx           # for brain-files / mes-repo
echo $MES_BRAIN_DB_PATH
echo $BRAIN_ROOT
echo $MES_REPO_ROOT
```

**"Database is locked"** — another process is writing the SQLite. Should resolve in seconds. If it persists, check whether the MES app is running on the OTHER machine (the lockfile in `Brain/mes-state/RUNNING.lock` will say).

**"No table named X"** — schema drift. Run the MES app once to migrate the DB to the latest schema, then retry.

**Skills feel slow on first call** — likely iCloud sync materializing files on demand. Run `/sync-check` to see what's pending. Right-click in Finder/Explorer → Always keep on this device for files you query often.

**Obsidian writes don't appear on the other machine** — iCloud sync delay (usually seconds, occasionally minutes). Check the iCloud icon in your menu bar / system tray.
