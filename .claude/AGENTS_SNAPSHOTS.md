# Agent definitions — live vs. snapshots

**`.claude/agents/` is the ONE live, canonical agent set.** It always holds the
latest agent definitions (planner, orchestrator, executors, git-agent, judge,
architect). Every Claude Code session and the multi-agent pipeline load from
here. When agent behavior changes, edit the files in `.claude/agents/` — nowhere
else.

## Frozen historical snapshots (do not edit; kept for revert/revive)

These directories are read-only point-in-time copies of how the agents worked at
earlier milestones. They are intentionally retained so a past configuration can
be inspected, compared, or restored. **Do not edit them and do not route the
pipeline to them** — they exist purely as recoverable history.

| Snapshot dir | What it captures |
|---|---|
| `.claude/agents_pre_opus48/` | The agent set immediately **before** the Opus 4.8 effort-tier migration (IMP-H1, PR #10). |
| `.claude/agents_old/` | An earlier agent generation kept before larger reworks. |
| `.claude/agents_copy2/` | A secondary backup copy of an earlier state. |

## How to revert the live agents to a snapshot

To restore a past configuration, copy the snapshot over the live dir, e.g.:

```bash
# inspect differences first
git diff --no-index .claude/agents_pre_opus48/planner.md .claude/agents/planner.md

# revert ALL live agents to the pre-4.8 snapshot
cp .claude/agents_pre_opus48/*.md .claude/agents/
```

(Adjust the source dir for whichever snapshot you want.) Commit the result on a
branch so the change goes through the normal PR/merge gate.

## Adding a new snapshot

Before a sweeping agent rework, copy the current live set into a new dated dir
(e.g. `.claude/agents_pre_<change>/`) so this table can point at it. Snapshots
are cheap and make any future rollback a one-line copy.
