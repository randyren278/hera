---
id: 01JZZSEEDPACKSPACEDRP00000
type: concept
title: "Spaced Repetition"
aliases: ["SRS"]
created: 2026-01-01T00:00:00
updated: 2026-01-01T00:00:00
tags: [seed]
pinned: true
---
> [!info] Scheduling reviews at increasing intervals so each recall happens near the point of forgetting.

Spaced repetition schedules a review just before predicted forgetting, so each successful recall pushes the next interval further out. The classic SM-2 algorithm uses **six** interval steps before a card is considered mature.

Implementations store scheduling state per card; [[Anki]] is the most widely used.
