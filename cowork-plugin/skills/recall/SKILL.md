---
name: recall
description: Search the shared Brain for prior thinking on a topic — Obsidian vault, claude-memory facts, brain-log entries, past trade analogs. Returns a synthesized answer citing specific notes. Trigger phrases include "have I thought about this", "what do I know about X", "recall my notes", "search the brain", "what did I conclude", "have I seen this before".
---

# Recall — Multi-Source Brain Search

The Brain holds four layers of knowledge:

- **`obsidian/Obsidian Vault/`** — long-form notes (Trades, Patterns, Learning, MarketContext, Daily, etc.)
- **`claude-memory/`** — durable facts (user profile, account rules, philosophy, project context)
- **`journal/brain-log.md`** — session-level running log, append-only
- **`journal/all_trades.csv` + `apex_grade_log.csv`** — trade data, queryable for analogs

This skill searches all four and synthesizes.

## Required context

Load `mes-context` first. Use `brain-files` MCP for file search/reads, `mes-brain` for DB queries if the topic has SQL data (`learning_history`, `market_patterns`, `agent_knowledge`).

## Inputs

If unclear: "What topic are you trying to recall?" — accept single string.

## Workflow

### 1. Search claude-memory (highest-authority facts)

`brain-files search_files` in `claude-memory/` for the topic. Read matched files (usually 1-3). These are your ground truth — if claude-memory says something, it supersedes Obsidian.

### 2. Search Obsidian vault

`brain-files search_files` under `Brain/obsidian/Obsidian Vault/` for:
- Filename matches
- Body text matches
- Frontmatter tag matches

Rank by:
- Match strength
- Folder relevance to topic (e.g. "setup X" → search Patterns/ and Strategies/ first)
- Recency (newer notes usually more relevant)

Read top 5 matches. Extract the most relevant 1-2 sentences from each.

### 3. Search brain-log

Read `Brain/journal/brain-log.md`. Grep for topic. Each session entry follows the `## YYYY-MM-DD HH:MM` format — return matching entries in full.

### 4. Search trade journal for analogs (if topic is a trade setup / pattern)

Use `mes-brain` MCP to query `journal_trades` / `market_patterns` / `learning_history`. For trade topics, query `all_trades.csv` via `brain-files` for historical analogs.

### 5. Synthesize

Build response:

```markdown
## What you've concluded / observed

<2-4 sentence synthesis — what you KNOW vs what's exploratory vs what's contradicted by data>

## Sources

**Claude memory (authoritative):**
- `<file>.md`: <verbatim relevant line>

**Obsidian:**
- [[note-filename]] — <one-line takeaway>
- [[note-filename]] — <one-line takeaway>

**Brain log:**
- <YYYY-MM-DD HH:MM>: <line or summary>

**Trade journal data:**
- <N matching trades: win rate, avg P&L, regime>

## Gaps or contradictions

<If sources disagree, call it out. If sparse coverage, say "not much data yet — would be good to /insight-capture after next session.">
```

### 6. Suggest next action

One-line suggestion:
- Strong consistent prior → "You've concluded this. Trust it or actively challenge it."
- Conflicting prior → "Sources disagree. Worth a fresh look — your earlier notes say X but recent data says Y."
- Sparse → "Not much here yet. Run /playbook-match for the data analog."
- Durable rule already in claude-memory → "This is a codified rule, not something to re-argue."

## Output format

Synthesis at top, sources middle, gaps + next at bottom. 30-50 lines.

## Don'ts

- Don't return raw search results. Synthesize.
- Don't fabricate note links — only `[[link]]` to actual vault filenames you verified.
- Don't claim to have searched a source that errored. Say so and proceed.
- Claude-memory rules outrank Obsidian impressions. If `feedback_account_separation.md` says "never mix accounts" and an old Obsidian note suggests combined math, the memory file wins.
