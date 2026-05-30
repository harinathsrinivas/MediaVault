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

## Merge into `main` — REQUIRES HUMAN APPROVAL (Checkpoint 1)

Merging into `main` is **gated on the user**. Agents and Claude Code may create the PR and push the branch, but **must STOP and get the user's explicit confirmation before merging**. Never run `gh pr merge`, `git merge` into `main`, or push to `main` autonomously — not even for docs or "trivial" changes.

- On the user's explicit approval, squash-merge (matches the repo's `(#N)` history): `gh pr merge <#> --squash`.
- Do **not** delete the branch at merge time — that happens later, under Checkpoint 2.
- After merge, sync local `main`: `git checkout main && git pull --ff-only`.

## Archiving a merged feature branch — REQUIRES HUMAN APPROVAL (Checkpoint 2)

Because we squash-merge, the detailed per-step commits and decisions live **only** on the feature-branch ref: the squash commit on `main` is not a descendant of them, `git branch --merged` won't even detect the branch, and the commits get garbage-collected if the ref is deleted. **Archive tags are therefore the standard way to keep the branch list clean without losing that history** — a tag is a permanent, GC-proof pointer. (Precedent: `git-agent` already archives multi-candidate branches via `candidates/step-N/<letter>-…` tags.)

After a branch is merged into `main`, **check with the user first**. On the user's confirmation:
1. Create an **annotated** `archive/<branch-name>` tag at the branch tip whose message includes the merge info **and the revive steps** (template below — the revive steps live *inside* the tag so they can never be lost).
2. Push the tag: `git push origin archive/<branch-name>`.
3. Delete the feature branch, local **and** remote, so the branch list stays clean. The tag preserves every commit permanently.

Archive tag message template:
```
Squash-merged to main via PR #<N> (squash <main-sha>). Archived <YYYY-MM-DD>.
Detailed pre-squash commits preserved here for debugging.

Revive as a branch:  git switch -c <branch-name> archive/<branch-name>
Inspect commits:     git log --oneline main..archive/<branch-name>
Browse at tip:       git checkout archive/<branch-name>
```

Commands:
```bash
git tag -a archive/<branch> <branch> -m "<message above>"
git push origin archive/<branch>
git branch -D <branch>                 # safe: the tag holds the commits
git push origin --delete <branch>
```

Reviving later is exactly the steps embedded in the tag message — read them with `git tag -n99 archive/<branch>`.

## Note for agents that load only on `.claude/` context
The `git-agent` (OP: CREATE_PR) and the `orchestrator` (Phase 3) both reference this file. Keep them in sync if these rules change.
