---
name: accountability-post
description: Generate a paste-ready daily accountability snippet for Discord/chat/email — today's grade, P&L, rules broken, streak, single commitment for tomorrow. Mirrors `daily_post.py` / `accountability_export.py`. Trigger phrases include "accountability post", "daily snippet", "post for discord", "share my day", "accountability export".
---

# Accountability Post — Shareable Daily Snippet

Generates a copy-paste accountability post Jaime can send to whatever group / chat / mentor / journal he uses. Public commitment is one of the strongest behavior-change levers.

## Required context

Always load `mes-context` first. Account: **APEX-565990-01**. Authoritative version: `python ~/mes-intel/tools/daily_post.py` (also copies to clipboard via `clip` on Windows).

## Workflow

### 1. Pull today's session data

Read `journal/all_trades.csv` (today's APEX trades) and `journal/rule_compliance.csv` (today's grade). If today's grade isn't in the file yet, compute it inline using the 10-rule logic from `/grade-day`.

### 2. Build the snippet

Format to render (the user pastes this verbatim):

```
📊 APEX Day <N> — <yyyy-mm-dd>

Trades: N · Net: $±X · Grade: <A/B/C/D/F> (<score>%)
Streak: <N sessions> · Balance: $<XX,XXX>
Distance to pass: $<X,XXX>

🟢 Worked: <one-line synthesized win — best trade or rule kept>
🔴 Broke: <one-line top rule failure or biggest loss reason>

Tomorrow: <single commitment from /grade-day>

#apex #futurestrading #mes
```

### 3. Variants

Offer 2-3 variants with different audiences:

**Discord** (default — emojis OK, casual):
- Use the format above

**Email/text** (no emojis, professional):
```
APEX Day N — yyyy-mm-dd

Trades N · Net $X · Grade <A/B/C/D/F> (<score>%)
Streak: <N>
Balance: $<X> · Distance to pass: $<X>

What worked: <line>
What broke: <line>

Tomorrow's commitment: <line>
```

**One-liner** (maximally tight, for status):
```
APEX D<N>: <grade> (<score>%) · $±X · streak <N>
```

### 4. Clipboard hint

End with:
```
On PC, this is auto-copied to clipboard by daily_post.py.
On Mac, select the snippet above and Cmd-C.
```

### 5. Optional save

Offer to save to `journal/accountability/<yyyy-mm-dd>.md` for archive. If yes, write via `brain-files` MCP.

## Output format

Three boxed snippets, then clipboard + save hint.

## Don'ts

- Don't make the post triumphant or self-deprecating. State the facts. Tomorrow's commitment.
- Don't include PERSONAL data — accountability is on the eval.
- Don't add hashtags Jaime didn't ask for.
- Don't include account number (APEX-565990-01) — keep public posts privacy-safe.
