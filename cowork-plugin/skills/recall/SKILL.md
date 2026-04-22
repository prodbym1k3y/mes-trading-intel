---
name: recall
description: Search the shared Brain for prior thinking on a topic — Obsidian vault notes, Claude memory, and trade journal patterns. Surfaces what you've previously concluded, observed, or tried so you don't relearn the same lesson. Trigger phrases include "have I thought about this before", "what do I know about X", "recall my notes on", "search the brain", "what did I conclude about".
---

# Recall — Cross-Source Search of the Brain

The Brain holds three layers of knowledge:

- **Obsidian vault** — long-form notes (insights, lessons, market structure observations)
- **Claude memory** — durable facts (trader profile, project context, common pitfalls)
- **mes_intel.db** — structured data (trades, regimes, patterns, agent learnings)

This skill searches all three and synthesizes a single answer. Use when the user is forming a thought and wants to know what they've already concluded.

## Required context

Load `mes-context` first. Use `brain-files` for filesystem search across `obsidian/Obsidian Vault/` and `claude-memory/`. Use `mes-brain` for SQL queries against the journal.

## Inputs to gather

The query topic. If unclear, ask:

- What are you trying to recall? (a setup type, a market regime observation, a prop firm rule, a lesson, etc.)

## Workflow

### 1. Search Obsidian vault

Use `brain-files` to list and read files in `obsidian/Obsidian Vault/`. Strategy:

- First, list all `.md` files (filenames often contain the topic).
- Filter by filename match on the query terms.
- For top 10 candidates, read the file and check body relevance.
- Also check note tags in YAML frontmatter against the query.

Capture: file path, title, the most relevant 1-2 sentences.

### 2. Search Claude memory

Read all `.md` files in `claude-memory/`. These are short and structured — load them all and grep for relevance.

Capture: file path, the relevant memory entry verbatim.

### 3. Search the trade journal

Use `mes-brain` to query `journal_trades`, `market_patterns`, and `learning_history` for entries tagged or referenced by the topic.

If the topic is a setup type or pattern name, query `market_patterns` for matching entries. If it's a regime observation, query `market_regimes`. If it's a lesson or rule, query `learning_history`.

Capture: a 1-line summary of what the data says (e.g. "12 trades matching, 58% WR, avg +$45").

### 4. Synthesize

Build a single response in this shape:

```markdown
## What you've concluded

<2-4 sentences synthesizing the prior thinking from Obsidian + memory.>

## Sources

**Obsidian:**
- [[<note title>]] — <one-line takeaway>
- [[<note title>]] — <one-line takeaway>

**Claude memory:**
- <file>: <verbatim entry>

**Brain DB:**
- <table>: <stat summary>

## Gaps

<If the topic has thin coverage in any source, name it. "No journal data on this setup yet — would be good to /grade-trade the next one and /insight-capture the result.">
```

The synthesis at the top is the value — sources support it but aren't the answer.

### 5. Suggest next action

If recall surfaces a clear pattern, end with one suggested next step:

- Conflicting prior conclusions → "Worth a fresh review — your earlier notes say X but recent trades say Y."
- Strong consistent prior → "You've concluded this before. Trust it or actively challenge it."
- Sparse data → "Not enough yet. Run /playbook-match to see if the journal has analogs."

## Don'ts

- Don't return raw search results. Synthesize.
- Don't claim to have searched a source you couldn't read. If `brain-files` errors, say so and proceed with what you got.
- Don't fabricate links to notes that don't exist. Only `[[link]]` to actual vault filenames you verified.
