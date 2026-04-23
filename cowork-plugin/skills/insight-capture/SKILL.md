---
name: insight-capture
description: Capture a trading insight, lesson, observation, or rule update into the shared Brain. Writes to the Obsidian vault using the existing folder structure (Trades, Patterns, Learnings, Daily, MarketContext, etc.) AND optionally appends a one-liner to brain-log.md or claude-memory if it's a durable fact. Trigger phrases include "save this", "note this", "log this lesson", "capture", "add to obsidian", "remember this", "append to brain log".
---

# Insight Capture — Persist to the Shared Brain

Jaime uses Obsidian + claude-memory as durable storage. Brain-log.md as the running session log. Each has a specific role — pick the right destination, don't dump everything everywhere.

## Required context

Always load `mes-context` first. Use `brain-files` MCP for filesystem access to Brain.

The Obsidian vault structure that already exists (don't reinvent — write into these folders):

```
Brain/obsidian/Obsidian Vault/
├── Trades/        ← per-trade write-ups (date_time_direction_X.md format)
├── Patterns/      ← reusable pattern definitions
├── MarketContext/ ← regime / structural observations
├── Strategies/    ← strategy ideas / write-ups
├── Learning/      ← session-level learnings
├── Learnings/     ← (separate folder — older?)
├── Performance/   ← reports + briefings
├── Daily/         ← daily reviews / journals
├── ML/            ← ML / data analysis notes
├── Templates/     ← note templates
├── System/        ← system docs
├── accountability/ ← accountability posts archive
```

Other key destinations:

```
Brain/journal/brain-log.md        ← session-level append-only running log
Brain/claude-memory/<file>.md     ← durable project-level facts (rare — only true rules)
```

## Inputs to gather

If unclear, ask via AskUserQuestion:

1. **The insight** — one to a few sentences
2. **Type** — one of:
   - `trade` — write-up of a specific trade → `Trades/`
   - `pattern` — reusable pattern definition → `Patterns/`
   - `market-context` → `MarketContext/`
   - `lesson` — session-level lesson → `Learning/` and append to `brain-log.md`
   - `daily-review` → `Daily/`
   - `accountability` → `accountability/` and offer to also `/accountability-post`
   - `strategy` → `Strategies/`
   - `rule-update` — actually a project-level fact (e.g. account rule change, system policy) → claude-memory

If unsure, default to `lesson` and append to `brain-log.md`.

## Workflow

### 1. Brain-log entry first (always for session-level stuff)

For `lesson`, `daily-review`, or `accountability` types, ALWAYS append to `Brain/journal/brain-log.md` using the existing format:

```markdown
## YYYY-MM-DD HH:MM (Phoenix)

**What happened:** ...
**What worked:** ...
**What didn't:** ...
**Pattern tagged:** ...
**To watch for:** ...
```

Use `brain-files` `read_text_file` then `write_file` to append (since edit_file may not handle appending cleanly).

### 2. Obsidian note

Filename pattern (kebab-case with date prefix):
- Trades: `YYYY-MM-DD_HHMM_<Direction>_<seqnum>.md`
- Patterns: `<pattern-slug>.md`
- Lessons: `YYYY-MM-DD - <slug>.md`
- Daily reviews: `YYYY-MM-DD.md`

Body template:

```markdown
---
type: <type>
created: <ISO datetime in Phoenix>
account: APEX-565990-01
tags: [<inferred tags from content>]
---

# <Title>

<The insight, written tightly. 1-4 paragraphs.>

## Context

<What was happening when this was noticed. Reference trade #, regime, time of day, account state.>

## Implication

<What changes — a behavioral rule, a slice to watch, a constraint, a new observation.>

## Related

<Backlinks via [[note-name]] — search the vault for related topics first.>
```

For `trade` type, supplement Context with trade specifics (entry/exit/size/ticks/duration) pulled from `journal/all_trades.csv`.

### 3. Cross-link

After writing, scan related folder for notes that should backlink. List them but DON'T auto-edit other notes — surface as a suggestion.

### 4. Claude memory (only if `rule-update`)

For things like "Apex changed daily loss limit to $1500" or "I'm now using ATAS v3" — durable project-level facts. Append a single line to the appropriate claude-memory file, don't dump narrative.

If creating a new memory file, follow the existing format:
```yaml
---
name: ...
description: ...
type: <user|project|reference|feedback>
---

<content>
```

Then update `claude-memory/MEMORY.md` index with a new bullet.

### 5. Confirm

```
Saved to:
  - Obsidian: <path under Brain/obsidian/Obsidian Vault/>
  - Brain log: appended (if lesson type)
  - Claude memory: <updated|not applicable>

Suggested next: 
  - Backlink to: [[note-1]], [[note-2]]
  - Run: /accountability-post (if it's a daily lesson)
```

## Don'ts

- Don't write to Obsidian without user actually wanting it persisted. If exploratory and they haven't said save, ask.
- Don't overwrite existing notes — append a date suffix or ask.
- Don't tag with more than 4 tags. Tag noise destroys search.
- Don't write trade entry/exit prices to a public-tagged note. Keep account/PNL details to private folders.
- Don't write to claude-memory for non-durable facts. That folder is for rules and structural truths only.
