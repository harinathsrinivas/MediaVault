---
title: Vault Guide — Obsidian vs Notion & cross-device sync
type: guide
feature: auto-rollback
tags: [claude, mediavault, vault, setup]
created: 2026-05-28
---

# Vault Guide — how to track this across iPad / iPhone / Mac / Windows

## Recommendation: **Obsidian** (free)

Your notes are Claude-generated **Markdown files in a git repo**. That makes the
choice easy:

| | Obsidian | Notion |
|---|---|---|
| Storage model | Plain `.md` files on disk — **the repo IS the vault** | Proprietary block database |
| Works with Claude Code output | **Directly** — Claude writes `.md`, Obsidian renders it, git syncs | Must **import** each file; re-import on every update; can't edit back to files |
| `[[wikilinks]]`, frontmatter, tags, backlinks, graph | **Native** | Not supported (wikilinks become plain text) |
| Free tier | App fully free; sync via git/iCloud is free | Generous free personal plan |
| Cross-device sync (free) | Via GitHub or iCloud (a little setup on mobile) | **Effortless, built-in** |
| Best when… | Your truth is files in git (**this is you**) | You want shared pages + relational databases |

**Verdict:** Obsidian. The only thing Notion does better for you is zero-setup
mobile sync — and that's solvable for free (below).

## Free cross-platform sync (no paid Obsidian Sync)

Your repo is already on GitHub (`harinathsrinivas/MediaVault`). Use that as the
single source of truth.

**Mac + Windows (easy):**
1. Clone the repo (or just keep your existing checkout).
2. Obsidian → *Open folder as vault* → point at the repo root (or at
   `docs/feature-auto-rollback/` if you want a focused vault).
3. Install community plugin **Obsidian Git** → enable auto pull on start +
   auto commit/push every N minutes. Done — edits sync through GitHub.

**iPhone + iPad — pick one:**
- **Option A — all-Apple, seamless:** keep the vault folder in **iCloud Drive**.
  Obsidian mobile opens iCloud vaults natively and it syncs to your Mac with no
  setup. Bridge Windows by keeping its copy in git (or iCloud for Windows).
- **Option B — git everywhere (fully free):** install **Working Copy** (iOS git
  client, free tier clones private repos), clone the repo, then in Obsidian
  mobile open the vault from the Working Copy folder via the Files app. Pull/push
  in Working Copy. Keeps 100% of content in git.

**Simplest mental model:** GitHub = truth. Desktop uses Obsidian Git; mobile uses
Working Copy (or iCloud for the Apple devices). Nothing proprietary, nothing paid.

## Notion-compatibility note

These files are still **importable into Notion** if you ever want to: headings,
tables, and `- [ ]` checkboxes convert to Notion blocks cleanly. Only the YAML
frontmatter (shows as text) and `[[wikilinks]]` (become plain text) don't carry
over. So nothing is lost by optimizing for Obsidian — Notion remains a fallback.

## How this vault is organized

- Top-level notes (`README`, `DECISIONS`, `FAILURE_ANALYSIS`,
  `RELATED_IMPROVEMENTS`, `SESSION_LOG`, `PLAN`) = the auto-rollback feature.
- [[_TRACKER]] = the live dashboard of prerequisite tasks.
- One **subfolder per prerequisite** (`C9-atomic-replace/`, `C11-…/`, …) — each
  holds that task's note (prompt + checklist + completion report) and, once work
  starts, its `PLAN.md` and any test notes. Each subfolder becomes the complete
  record of that task.
