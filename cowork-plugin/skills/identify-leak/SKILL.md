---
name: identify-leak
description: Hunt for what's quietly draining P&L. Examines all trades for systematic losing patterns — time-of-day, regime mismatches, position sizing inconsistencies, exit timing, and entry-trigger quality. Returns the top 3 leaks ranked by $ impact. Trigger phrases include "find my leaks", "what's costing me money", "where am I bleeding", "P&L drain", "fix what's not working".
---

# Identify Leak — Where the Money Is Quietly Going

Most traders have a few systematic errors that account for the bulk of their losses. This skill mines the journal to find them, ranks by dollar impact, and proposes the smallest behavior change that closes each one.

## Required context

Load `mes-context` first. Use `mes-brain` for queries. Default lookback is the last 30 trading days (override on request).

## Workflow

### 1. Hypothesis generation

Run these specific tests against the journal. Each is a candidate leak.

#### Leak A: Bad time-of-day exposure

Bucket trades into 30-min windows. Compute net P&L per bucket. Any bucket with >= 5 trades and net P&L > 1 standard deviation worse than the average bucket is a candidate.

#### Leak B: Regime mismatch

For each regime, find strategies/setups that have negative expectancy in that regime but were taken anyway. Quantify the cumulative loss.

#### Leak C: Counter-system entries

Find trades where the ensemble bias was OPPOSITE the trade direction at entry, OR where ensemble confidence was below `min_confidence` at entry. Sum the P&L of these trades — almost always negative on net.

#### Leak D: Wide stops on losers

Compare planned stop sizes (if recorded) or actual losing trade sizes to the rolling avg loss. Outliers (>2x avg) suggest stops being held too wide or moved.

#### Leak E: Premature exits on winners

For winning trades, compare actual hold time to the average winning hold time for that setup type. Trades exited at <50% of typical hold time may be leaving money on the table — quantify by comparing to the eventual MFE if reachable in the data.

#### Leak F: Add-on/scaling errors

If trades have parent/child relationships (or sequential trades within minutes on same side), identify cases where adding to a loser turned a small loss into a big one.

#### Leak G: Account-rule near-misses

Trades that, given the account's rules, should not have been taken (e.g. trading after hitting a daily loss soft limit). Even if they didn't breach, the risk-adjusted expectancy is negative.

### 2. Rank by impact

For each leak with sufficient sample (N >= 5), compute:

- **Estimated annual cost** (extrapolate from the lookback window)
- **Confidence in the leak** (statistical strength)
- **Fixability** (is it a behavioral change or a system change)

Sort by estimated annual cost. Take top 3.

### 3. Render the top 3 leaks

For each:

```
LEAK <N>: <name>
  Estimated annual cost: -$X
  Sample: N trades
  Pattern: <one sentence>
  Evidence: <2-3 bullets with stats>
  Fix: <single specific behavioral change>
  Test: <how the user knows it's working in 2 weeks>
```

### 4. The "easiest dollar"

Among the top 3, identify the one with the lowest behavioral cost — the leak that requires the smallest change to fix. Surface it as the recommended starting point.

End with a reminder: "If you adopt the fix, run `/weekly-review` next week and check whether the leak's contribution dropped."

## Output format

Headline ("Top 3 leaks, total estimated annual cost: -$X"), then the 3 leak blocks, then the "easiest dollar" recommendation.

## Don'ts

- Don't surface leaks with N < 5 — too noisy.
- Don't propose fixes that require system rewrites (that's not a leak, that's an upgrade).
- Don't dress up bad luck as a leak. If the trades had system alignment and good entries but lost to market noise, that's variance, not a leak.
- Don't suggest fixing more than the top 3 at once. Sequential focus beats parallel chaos.
