---
name: sync-check
description: Health check the shared Brain — verifies iCloud sync state, lockfile status, DB freshness, Obsidian and memory accessibility from this machine. Use when switching between Mac and PC, when something feels stale, or when troubleshooting connection issues. Trigger phrases include "sync check", "is the brain healthy", "check brain status", "is the other machine running", "lockfile check".
---

# Sync Check — Brain Health and Multi-Machine State

The Brain folder syncs via iCloud between Mac and PC. The MES app uses a lockfile to prevent both machines from writing to the SQLite DB simultaneously. This skill verifies all of that is working before the user tries to start the app.

## Required context

Load `mes-context` first. Use `brain-files` MCP for filesystem checks, `mes-brain` MCP for DB freshness queries.

## Workflow

### 1. Brain folder presence

Verify the Brain folder is accessible via `brain-files`:

- List the Brain root.
- Check for `mes-state/`, `obsidian/`, `claude-memory/` subdirectories.

If any are missing or empty, the Brain isn't synced to this machine yet — surface that loudly.

### 2. Lockfile state

Read `mes-state/RUNNING.lock` if it exists. Parse `hostname`, `pid`, `timestamp`.

| State                                                    | Verdict                                          |
|----------------------------------------------------------|--------------------------------------------------|
| File doesn't exist                                       | App is not running anywhere                      |
| `hostname` matches this machine                          | App is or was running here                       |
| `hostname` is different machine, timestamp < 2h ago      | **Other machine is running the app — don't start** |
| `hostname` is different machine, timestamp > 2h ago      | Stale lock, safe to clear                        |

### 3. DB freshness

Use `mes-brain` MCP to query the DB:

- Get the most recent timestamp from `journal_trades`, `market_regimes`, `learning_history`.
- Compare to current Phoenix time.
- Compute "data age" — the gap between the latest DB write and now.

If data age is > 30 minutes during Phoenix RTH (6:30 AM – 2:00 PM weekdays), flag as suspicious — either the app crashed, the user isn't trading, or sync is lagging.

### 4. Obsidian + memory accessibility

Spot check by listing:

- `obsidian/Obsidian Vault/` — count `.md` files total. Expected >100 (rich vault).
- `claude-memory/` — check `MEMORY.md` + the key project files exist: `user_trading_profile.md`, `feedback_account_separation.md`, `feedback_system_philosophy.md`, `project_apex_eval_analysis.md`, `project_current_edges.md`, `project_current_leaks.md`.
- `journal/` — check `all_trades.csv`, `apex_grade_log.csv`, `rule_compliance.csv`, `brain-log.md` all exist.

Confirm none is empty or missing (would indicate PC-side sync hasn't completed).

### 5. iCloud sync hint (Mac only)

If running on Mac, you can read filesystem extended attributes to see if any Brain files are still placeholders (cloud-only). Files with the `com.apple.icloud.notdownloaded` xattr aren't fully local. You can check via `bash` if available, or just suggest the user look at the menu-bar iCloud icon for pending operations.

On PC, the equivalent is the cloud-status icon in File Explorer next to each file (cloud = not downloaded, checkmark = local).

### 6. Verdict

```
BRAIN STATUS: <green | yellow | red>

Brain folder:    <green> 3 subdirs present
Lockfile:        <green | warn | red> <one-line state>
DB freshness:    <green | warn> last write <N> min ago
Obsidian:        <green> N notes accessible
Claude memory:   <green> MEMORY.md present
iCloud sync:     <green | warn> <one-line>

Safe to start MES app: <yes | no>
Reason: <one line>
```

If `Safe to start: no`, give the specific recovery action:

- Other machine running: "Stop the app on `<hostname>` first, or wait."
- Stale lock: "Run: `rm <path-to-lockfile>`"
- Sync incomplete: "Wait for iCloud to finish (check menu bar icon)."

## Output format

Single status block (above). 10 lines or less. This is a glance-and-go skill.

## Don'ts

- Don't auto-delete the lockfile. Always present the rm command for the user to run consciously.
- Don't try to "fix" sync issues — iCloud sync is opaque from a process standpoint. Surface the symptom, recommend the user-side action.
- Don't run heavy DB queries here. A few `SELECT MAX(timestamp)` calls are enough.
