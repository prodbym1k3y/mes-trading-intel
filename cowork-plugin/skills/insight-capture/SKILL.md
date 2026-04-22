---
name: insight-capture
description: Capture a trading insight, observation, or lesson into the shared Brain — writes a structured note to the Obsidian vault and optionally appends a one-liner to Claude memory if it's a durable project-level fact. Trigger phrases include "save this insight", "note this for later", "log this lesson", "capture this", "add to obsidian", "remember this".
---

# Insight Capture — Persist Findings to the Brain

The user has just realized something worth keeping — a recurring losing pattern, a setup that's working, a market structure observation, a rule change for a prop account. Write it to the right place in the Brain so both machines (Mac + PC) and future Claude sessions can find it later.

## Required context

Load `mes-context` first. Use the `brain-files` MCP for filesystem access to the Brain folder. The Brain root is whatever the `BRAIN_ROOT` env var resolves to (typically `~/Library/Mobile Documents/com~apple~CloudDocs/Brain/` on Mac and `%USERPROFILE%\iCloudDrive\Brain\` on PC).

## Inputs to gather

If not already obvious from context, ask (use AskUserQuestion):

1. **The insight itself** — one to a few sentences in the user's words.
2. **Type** — one of:
   - `trade-pattern` (a recurring setup, win or lose)
   - `market-structure` (regime behavior, level dynamics, dealer flow observation)
   - `rule-update` (prop firm rule change, account state change, methodology adjustment)
   - `lesson` (a personal trading mistake / improvement)
   - `idea` (a new strategy idea, agent improvement, app feature)

If the user is clearly mid-conversation about one of these, infer the type instead of asking.

## Workflow

### 1. Decide the destination

| Insight type        | Destination                                                       |
|---------------------|-------------------------------------------------------------------|
| `trade-pattern`     | `obsidian/Obsidian Vault/Trading/Patterns/`                       |
| `market-structure`  | `obsidian/Obsidian Vault/Trading/Market Structure/`               |
| `rule-update`       | `obsidian/Obsidian Vault/Trading/Accounts/` + Claude memory       |
| `lesson`            | `obsidian/Obsidian Vault/Trading/Lessons/`                        |
| `idea`              | `obsidian/Obsidian Vault/Ideas/`                                  |

If the destination directory doesn't exist yet, create it.

### 2. Build the note

Filename pattern: `YYYY-MM-DD <slug>.md` where `<slug>` is a 3-6 word kebab-case summary. Use Phoenix date.

Body template:

```markdown
---
type: <type>
created: <ISO datetime in Phoenix>
tags: [<inferred tags>]
---

# <Title>

<The insight, written tightly. 1–4 paragraphs.>

## Context

<What was happening when this was noticed. Reference DB tables / agents / regime where useful.>

## Implication

<What changes because of this — a behavioral rule, a strategy weight, an account constraint, a watch item.>

## Related

<Links to other vault notes via `[[note-name]]` — search the vault for related topics first.>
```

For `trade-pattern` and `market-structure`, query `mes-brain` for any supporting stats (win rate, sample size, regime breakdown) and include a short evidence section.

### 3. Cross-link

After writing the new note, use `brain-files` to scan related folders for notes that should backlink to this one. If found, suggest the user add the backlink (don't auto-edit other notes — that's their territory).

### 4. Update Claude memory (only if `rule-update`)

For rule changes (e.g. "prop firm X changed daily loss to $1500"), append a single-line update to the appropriate file in `claude-memory/` so future sessions know. Use the file naming the user already established (`user_trading_profile.md`, `project_mes_intel.md`, etc. — check what exists first).

Don't dump the whole insight into memory. Memory is for durable facts, not narrative.

### 5. Confirm

Render a short confirmation:

```
Saved: <relative path from Brain/>
Type: <type>
Memory updated: <yes|no>
```

## Don'ts

- Don't write to Obsidian without the user actually wanting it persisted. If the conversation is exploratory and they haven't said "save this", ask first.
- Don't overwrite existing notes silently. If a file with the proposed name exists, append a `(2)` suffix or ask.
- Don't tag the note with more than 4-5 tags. Tag noise destroys search.
- Don't write secrets, account numbers, or API keys to the vault.
