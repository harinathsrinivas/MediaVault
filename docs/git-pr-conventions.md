# Git & PR Conventions

Canonical rules for branches, commits, and pull requests in this repo. **All agents and contributors follow these.** Referenced from the project `CLAUDE.md` so every Claude Code session and subagent loads it.

## Branches
- Branch from up-to-date `origin/main`.
- Name `<type>/<short_name>` where type ∈ `feature | fix | refactor | test | chore`. Lowercase, `_`/`-`, under 50 chars.
- Never commit or push directly to `main` — changes reach `main` only via a merged PR.

## Commits
- Imperative subject. Reference the IMP task in the body: `Refs: improvements_tier<X>.md IMP-<XN>` when applicable.
- End every commit message with:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

## Pull requests

### Title — MUST include the IMP code when the task maps to one
Format: `<type>: <short summary> — IMP-<XN>`

If the work implements/relates to a tracked improvement task (e.g. `IMP-C2`, `IMP-G1`, `IMP-H1`), the IMP code **must** appear in the title. If the work maps to several, list them (`IMP-A1/A2`). If no IMP applies (pure chore with no tracked task), omit the code — but check the tier files first.

Examples (history): `Refactor/extract mvcommon - IMP A1`, `Feature/push partial atomic rename - IMP-G1`, `Feature/adb selenium retry - imp-C2`.

### Body — MUST follow this order
1. **Auto-generated Claude Code summary** — the normal concise PR description: a short Summary, the key changes, and a Test plan / verification.
2. **`## Original task prompt`** — the **complete, verbatim** task prompt the user gave that kicked off this work. Do not paraphrase, summarize, or trim it. This preserves intent and context for future readers. (See PRs #7–#9 for the precedent of carrying the originating prompt in the PR body; the only change is that the Claude summary now comes first.)
3. The standard trailer last:
   ```
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

### Skeleton
```markdown
## Summary
<what this PR does, 2–5 bullets>

## Changes
<key files / behaviors changed>

## Test plan
<commands run / how to verify>

---

## Original task prompt
> <the full, verbatim initial prompt the user gave for this task>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Merge
- Squash-merge into `main` (matches the repo's `(#N)` history).
- Delete the branch after merge.
- After merge, sync local `main` (`git checkout main && git pull --ff-only`).

## Note for agents that load only on `.claude/` context
The `git-agent` (OP: CREATE_PR) and the `orchestrator` (Phase 3) both reference this file. Keep them in sync if these rules change.
