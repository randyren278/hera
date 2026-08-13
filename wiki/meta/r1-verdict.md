---
type: meta
title: "R-1 Verdict — Thinking-Block Availability"
aliases: []
tags: [design-decision]
verdict: tier2-only
---
# R-1 Verdict

**Decision:** disable Tier-1 citation scoring; count only final-answer wikilinks
(Tier 2).

**Why:** Claude Code 2.1.201 does not persist thinking-block content in the
transcript JSONL in a form we can parse — even at max effort, the `thinking`
field is either absent or truncated. Tier-1 scoring would silently score
nothing.

**Toggle:** `.claude/hooks/stop_score.py` — `TIER1_ENABLED = False`. Flip to
`True` when a Claude Code release starts persisting parseable thinking blocks.
