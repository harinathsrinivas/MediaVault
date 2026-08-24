# Edge case — `cmd_replace` PONR journal-lock race

> **A transient lock on `.mediavault_txn.json` during `mark_point_of_no_return()` is
> misdiagnosed as a locked media file, retried as though the master rename had not
> happened, and ends as a false `❌ IRREVERSIBLE` hard-fail.**
>
> Observed 2026-08-24 on `mov-kor-2003-ataleoftwosisters` while archiving the
> WavPack-remuxed 62.6 GB master (see [`../edge-case-unsplittable-tracks/README.md`](../edge-case-unsplittable-tracks/README.md)
> for why that file was remuxed first). **No data was lost.** The mechanism spec is
> [`../feature-auto-rollback/ROLLBACK_MECHANISM.md`](../feature-auto-rollback/ROLLBACK_MECHANISM.md).
>
> 🚨 **If you are here mid-incident: do NOT run `fetch_restore` yet.** Read
> [§2](#2-before-anything-else-do-not-run-fetch_restore) first — the failure prints advice that
> is actively harmful in this case.
>
> ⚠️ **The fix touches change-gated code and is deliberately NOT implemented.** Tracked as
> **IMP-R10** in [`../../improvements/improvements_tierR.md`](../../improvements/improvements_tierR.md).
> See [§10](#10-proposed-fix--change-gated-do-not-implement-unasked).

---

## 1. What the operator saw

`prep_push_rep mov-kor-2003-ataleoftwosisters … SIZE_MB 6000`. Steps 1 and 2 succeeded
completely — the deep scan and whole-file hash ran, the file split into **10 chunks of
~6 GB**, all 10 pushed to the device, `✅ SUCCESS.` Then step 3:

```
   > 🎬 Generating dummy video: A.Tale...NAHOM.mkv.dummy_tmp.mkv
   ✅ Dummy video created: A.Tale...NAHOM.mkv.dummy_tmp.mkv
     ⚠️ File busy or locked. Retrying... (1/3)
❌ Error removing file: [WinError 2] The system cannot find the file specified: 'C:\Media\...\A.Tale...NAHOM.mkv'
❌ IRREVERSIBLE: replace failed after the commit point for mov-kor-2003-ataleoftwosisters.
   > The original is no longer in place (C9 stale-sweep recovers it next run).
   > To recover the file from the cloud: fetch_restore mov-kor-2003-ataleoftwosisters
❌ Auto-Pilot Stopped: mov-kor-2003-ataleoftwosisters archived (original committed) — replace failed past the point-of-no-return: rollback() called after point-of-no-return
```

Both operator-facing error messages are wrong about what failed. Nothing was being
*removed*, and the file that was *busy or locked* was not the media file. See
[§12](#12-the-messages-actively-misled).

## 2. Before anything else: do NOT run `fetch_restore`

The single most expensive mistake available at this moment is to follow the instruction the
failure prints.

`cmd_replace`'s post-PONR handler emits, at `main.py:5453` and again as the structured
`resume_cmd` of the `RollbackHardFail` at `main.py:5454-5458`:

```python
print(f"   > To recover the file from the cloud: fetch_restore {manual_id}")   # main.py:5453
raise RollbackHardFail(
    state=f"{manual_id} archived (original committed)",
    reason=f"replace failed past the point-of-no-return: {e}",
    resume_cmd=f"fetch_restore {manual_id}",                                   # main.py:5457
)
```

Following it would have pulled **62.6 GB back down from Google Photos over Selenium** —
hours of driving the fetch pipeline, ten chunk downloads, then a merge — to recover a file
that **was sitting untouched on the local disk the entire time**, one filename away, as
`A.Tale...NAHOM.mkv.tobedeleted`.

The message is not a bug in itself. It is correct for the failure mode it was written for:
a *genuine* post-PONR loss, where the master really is gone and the cloud copy really is
the only one. It is simply wrong for this failure, where the PONR was crossed in memory but
the master never left the volume.

> ### The check to run first
>
> **Look for `<original>.tobedeleted` in the entry's folder.**
>
> - **Present** → the master is local and intact. `fetch_restore` is exactly the wrong
>   command. Use the drill in [§7](#7-recovery-procedure-performed-reusable) instead.
> - **Absent** → the two-rename swap really did complete and something later failed;
>   `fetch_restore` may genuinely be the right call.

Note that the two printed lines **contradict each other**. `main.py:5452` says the C9
stale-sweep recovers the original on the next run — which is true, and which means the
local file is still there — and then `main.py:5453` immediately tells the operator to pull
it from the cloud. An operator reading top-to-bottom under stress will act on the second.

## 3. The smoking gun

The folder was left holding **two** journal files:

| File | Size | Content |
|---|---|---|
| `.mediavault_txn.json` | 276 B | `"crossed_ponr": false` |
| `.mediavault_txn.json.tmp` | 275 B | `"crossed_ponr": true` |

The one-byte difference is exactly `false` → `true`. Both carry the same single record
(`create_file` of `…NAHOM.mkv.dummy_tmp.mkv`).

That orphan `.tmp` is the whole diagnosis. `_flush()` (`main.py:825-833`) persists the
journal as **write-temp → `fsync` → `os.replace`**:

```python
tmp = self.path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f); f.flush(); os.fsync(f.fileno())
os.replace(tmp, self.path)                                   # main.py:833
```

The PONR-marked payload reached the `.tmp` and the `os.replace` onto the live journal
**failed**. The durable write never landed. Meanwhile `mark_point_of_no_return()`
(`main.py:862-864`) sets the in-memory flag *before* flushing:

```python
def mark_point_of_no_return(self):
    self.crossed_ponr = True      # main.py:863 — in-memory, unconditional
    self._flush()                 # main.py:864 — may raise
```

So the process ended with `crossed_ponr = True` in memory and `false` on disk. Likely
lock source: antivirus or the Windows Search Indexer touching the folder immediately
after a 62 GB burst of chunk writes — see [§9](#9-trigger-conditions-and-likelihood).

## 4. How that becomes a hard fail

The retry loop at `main.py:5372-5402` wraps **chmod + rename + journal-flush** in one
`try`:

```python
for attempt in range(3):                                  # main.py:5375
    try:
        os.chmod(original, stat.S_IWRITE)                 # main.py:5378
        os.rename(original, tobedeleted)                  # main.py:5385  ← PONR seam
        journal.mark_point_of_no_return()                 # main.py:5387
        moved = True
        break
    except PermissionError:                               # main.py:5390
        print(f"     ⚠️ File busy or locked. Retrying... ({attempt + 1}/3)")
        time.sleep(1)
    except Exception as e:                                # main.py:5393
        print(f"❌ Error removing file: {e}")
        journal.rollback(library)  # still pre-PONR — reversible
        return False
```

| Step | What happened |
|---|---|
| Attempt 1 — `os.chmod` | ok |
| Attempt 1 — `os.rename` @5302 | **succeeded.** The master really did move to `.tobedeleted` |
| Attempt 1 — `mark_point_of_no_return()` @5304 | raised `PermissionError` from the blocked `os.replace` @761 |
| → `except PermissionError` @5307 | Caught. The handler exists to retry a **blocked rename**, so it prints `File busy or locked. Retrying... (1/3)` and loops |
| Attempt 2 — `os.chmod(original)` @5295 | `FileNotFoundError` / `WinError 2` — `original` no longer exists, attempt 1 renamed it away |
| → `except Exception` @5310 | Prints `Error removing file`, calls `journal.rollback(library)` @5312 under the comment `# still pre-PONR — reversible` |
| `rollback()` @795-800 | `self.crossed_ponr` is `True` in memory → `raise RuntimeError("rollback() called after point-of-no-return")` @800 |
| Outer handler @5364-5375 | Sees `journal.crossed_ponr` → prints the `❌ IRREVERSIBLE` banner and raises `RollbackHardFail(reason=…: {e})`, embedding the `RuntimeError` text — which is exactly the last line the operator saw |

**The defect in one sentence:** `mark_point_of_no_return()` sits inside the retry scope
of an `except PermissionError` handler written to retry the master rename, so a transient
lock on the *journal* file is misattributed to the *media* file and retried as though the
rename had never happened.

The comment at `main.py:5395` — `still pre-PONR — reversible` — is false in this path.
By the time attempt 2 reaches it, the PONR has been crossed in memory.

## 5. Why it was survivable

Nothing was lost. The two-rename pattern is what saved it:

| | State after the failure |
|---|---|
| Master | Safe at `A.Tale...NAHOM.mkv.tobedeleted`, **62,634,772,667 bytes** |
| Chunks | All 10 already on the device |
| Library | `uploaded: True`, `split_info` with all 10 chunk hashes |
| Dummy | Orphan `…dummy_tmp.mkv` (never renamed into place) |

The `IRREVERSIBLE` banner described the **journal's** state, not data loss. It is
technically accurate — the rename *had* committed — but it reads as catastrophic and is
not.

## 6. How to recognise this failure

Four signatures, all cheap to check. If you see the first three together, this is the bug:

1. **An orphan `.mediavault_txn.json.tmp` sitting next to `.mediavault_txn.json`.** The
   `.tmp` should never survive a successful `_flush()` — `os.replace` consumes it.
2. **The two journals differ only in `crossed_ponr`** (`true` in the `.tmp`, `false` in
   the live file), and are ~1 byte apart in size.
3. **`<original>.tobedeleted` present while `<original>` is absent.** The master moved but
   the dummy never took its place.
4. **`File busy or locked` immediately followed by `WinError 2` naming the SAME path.** A
   real lock retries against a file that still exists; this pair means the path vanished
   between attempts, i.e. the previous attempt's rename succeeded.

```powershell
$D = "C:\Media\Movies\Korean\A Tale of Two Sisters (2003) {tmdb-4552}"   # the entry's folder

Get-ChildItem -Force $D | Select-Object Length, Name      # look for .tmp, .tobedeleted, dummy_tmp
Get-Content "$D\.mediavault_txn.json"                     # expect crossed_ponr: false
Get-Content "$D\.mediavault_txn.json.tmp"                 # expect crossed_ponr: true
```

Signature 3 alone (a `.tobedeleted` with no orphan `.tmp`) is the *ordinary* torn-replace
case that C9 already self-heals — not necessarily this bug.

## 7. Recovery procedure (performed, reusable)

The same lock can recur, so this is the drill:

1. **`python main.py recover <id>`** — the on-disk journal still said
   `crossed_ponr: false`, so `recover_journal()` replayed the single `create_file`
   inverse (deleted the orphan `dummy_tmp`) and removed the journal:
   `✅ Recovery complete — pre-command state restored.`
2. **Delete `.mediavault_txn.json.tmp` by hand.** `recover` does not clean it — it is
   residue of the failed `os.replace` and is not a journal `recover_journal()` reads.
3. **`python main.py replace <id>`** — the C9 **stale sweep** (`main.py:5354-5366`) saw
   `.tobedeleted` present with `original` absent, restored the master
   (`⚠️ Recovered interrupted replace: restoring original from …`) and aborted **by
   design**: `❌ replace aborted — original restored. Please retry.`
4. **`python main.py replace <id>`** again — clean run, `✅ Replaced/Archived`.

Verified end state: dummy in place (**9,672 bytes**), entry `status: archived`,
`uploaded: True`, 10 chunks + `mvmeta` sidecar on the device, **62.6 GB reclaimed**
(C: 70.5 GB → 125.6 GB free).

> **Step 1 is optional in this variant.** `RollbackJournal`'s open path auto-recovers a
> leftover **pre-PONR** journal (`main.py:796-797`, the IMP-R7 behaviour), so going
> straight to `replace` would have handled it too. Running `recover` explicitly first is
> still preferable — it makes the state transition visible instead of folding it into an
> unrelated command's output.

> **The self-healing design worked.** The two-rename pattern, the C9 stale sweep, and
> `recover` together walked a torn commit back to a clean state with zero data loss and
> no manual file surgery beyond deleting one orphan `.tmp`. **The bug is in the retry
> loop's error attribution, not in the rollback architecture.**

## 8. Why the recovery worked — and the variant where it would not

This is the subtle part, and it is worth internalising before touching the code.

**The recovery in §7 succeeded *because the durable write failed*.** `recover_journal()`
refuses to act on a journal that crossed its PONR (`main.py:957-960`):

```python
if data.get("crossed_ponr"):
    print(f"   > ℹ️ Journal {TXN_JOURNAL_NAME} crossed its point-of-no-return — "
          "nothing to roll back; leaving it for inspection.")
    return False
```

Because the `os.replace` never landed, the on-disk journal still read `false`, so `recover`
was willing to replay the inverse. Had the flush *succeeded* and the failure occurred a
moment later, the on-disk journal would read `true` and `recover` would have declined —
correctly, by its own contract.

That does **not** leave the operator stranded, but the recovery path is different:

| Case | `os.rename` | `_flush()` | On-disk journal | Master at | `recover <id>` | Next `replace <id>` |
|---|---|---|---|---|---|---|
| **A** — designed pre-PONR failure | ✗ blocked | not reached | `false` | original path | replays inverses, removes journal | normal clean run |
| **B** — **this incident** | ✓ | ✗ **blocked** | `false` *(stale)* | `.tobedeleted` | acts: deletes orphan dummy, removes journal | C9 sweep restores master → aborts "Please retry"; run again → clean |
| **C** — mirror image | ✓ | ✓ | `true` | `.tobedeleted` | **declines** @885 — no in-band recovery | journal-open preserves the crossed journal aside @718-722, C9 sweep restores master → aborts; run again → clean |
| **D** — happy path | ✓ | ✓ | removed on commit | dummy in place | n/a | n/a |

In **case C** the crossed journal is not a dead end: `RollbackJournal`'s open path renames
it to a timestamped sibling `.mediavault_txn.<ts>.json` via `_preserve_leftover()`
(`main.py:790-794`, `735-750`) and continues, so the C9 stale sweep still runs and still
restores the master. The practical difference is that **`recover` is useless in case C** —
the drill becomes `replace` twice, with no step 1 — and the operator is left with a
preserved journal file to clean up manually.

Both B and C recover. Neither loses data. But they recover through *different* mechanisms,
and a future fix must not break either one.

## 9. Trigger conditions and likelihood

The lock was on the **small JSON journal**, not on the 62 GB media file. It landed
immediately after ~62 GB of chunk writes into that same folder — the classic window in
which Windows Defender's real-time scanner or the Search Indexer opens newly-written files
for inspection. `os.replace` onto a file another process holds open fails with
`PermissionError` on Windows (unlike POSIX, where the rename would succeed).

The uncomfortable consequence: **this bug is more likely on large, slow archives.** More
bytes written into the folder means a wider window for a scanner to be mid-pass when
`mark_point_of_no_return()` fires. Those are exactly the runs where:

- a spurious `❌ IRREVERSIBLE` banner is most alarming, and
- acting on the `fetch_restore` advice from [§2](#2-before-anything-else-do-not-run-fetch_restore)
  costs the most — tens of GB and hours of Selenium-driven downloading.

Small entries are comparatively safe. A 2 GB episode presents a far narrower window than a
62 GB remux.

Also relevant: **`mvcommon.retry()` was never applied to this loop.** The tier C header
records this explicitly ([`../../improvements/improvements_tierC.md`](../../improvements/improvements_tierC.md),
line 7): *"`mvcommon.retry()` (IMP-C2, done) wraps ADB push+mv (3 attempts, 1/4/16 s +
jitter) and the Selenium trigger (one retry). `cmd_replace`'s 3-retry PermissionError loop
predates it."* This hand-rolled loop is the last of its kind, and its 1-second flat sleep
is both shorter and less jittered than the 1/4/16 s backoff the rest of the codebase uses.

## 10. Proposed fix — CHANGE-GATED, do not implement unasked

> ### ⚠️ STOP — this is gated code
>
> [`CLAUDE.md`](../../CLAUDE.md) and [`ROLLBACK_MECHANISM.md` §10](../feature-auto-rollback/ROLLBACK_MECHANISM.md)
> (line 286) list **"the point-of-no-return locations or the `mark_point_of_no_return()`
> placement"** among the things that must not be modified without an explicit user
> decision. Any change here MUST first state exactly what differs from the documented
> behavior and be approved as a decision. This section records candidates for that
> conversation — it is **not** an approved plan.
>
> Tracked as **IMP-R10**. The user's decision on 2026-08-24 was explicitly *"document more
> — can fix later"*.

Candidate shapes, presented without a recommendation:

**(a) Narrow the `try`.** Keep `mark_point_of_no_return()` where it is, but scope the
`except PermissionError` retry to the `os.rename` call alone, so only a genuinely blocked
rename triggers retry-and-sleep. Smallest diff; leaves the call site untouched.

**(b) Move the marker out of the loop.** Call `mark_point_of_no_return()` after the retry
loop has exited, once the rename is known to have succeeded. Cleanest separation of "did
the commit happen" from "did we record that it happened", but it moves the marker, which is
the most explicitly gated element.

**(c) Make a post-rename flush failure non-fatal.** Treat the rename as the real commit and
the journal as a record of it, so a blocked `_flush()` warns rather than aborts. This is
the most invasive of the three: it changes the journal's **durability semantics**, not just
control flow, and it interacts with case C in [§8](#8-why-the-recovery-worked--and-the-variant-where-it-would-not).

A fourth, orthogonal option: re-check `os.path.exists(original)` at the top of each attempt
so a completed rename is never re-attempted. This does not fix the misattribution, only its
worst symptom.

**Open question for the rollback owner** (deliberately not answered here): should a failure
to *durably persist* the PONR marker be fatal at all, given the rename has already
committed and the C9 stale sweep self-heals the file state on the next run? That is a
semantics question about the journal's durability contract, not a code-shape question, and
it belongs in the rollback decision record rather than in this incident write-up.

## 11. What to verify when fixing

Acceptance criteria for whoever picks up IMP-R10. Re-read the change-gate spec
([`ROLLBACK_MECHANISM.md` §10](../feature-auto-rollback/ROLLBACK_MECHANISM.md)) **before**
writing code, and state the delta as a decision.

- [ ] **The torn-commit state is still recoverable** by the §7 drill: `recover` → delete
      orphan `.tmp` → `replace` → `replace`.
- [ ] **Case C still recovers too** (see [§8](#8-why-the-recovery-worked--and-the-variant-where-it-would-not)):
      a journal that genuinely crossed its PONR is preserved aside on journal-open, and the
      C9 sweep still restores the master. A fix that only exercises case B is incomplete.
- [ ] **The C9 stale sweep (`main.py:5354-5366`) keeps working** in both directions — the
      redundant-leftover branch and the restore-and-abort branch.
- [ ] **`recover_journal()`'s refusal on a crossed journal (`main.py:957-960`) is
      preserved.** That refusal is a correctness property, not an inconvenience; do not
      relax it to make case C easier.
- [ ] **`pytest tests/smoke -q` stays green**, plus a regression test that simulates a
      `PermissionError` from `_flush()` after a successful rename and asserts no
      `IRREVERSIBLE` banner and no `RuntimeError`.
- [ ] **The misleading messages in [§12](#12-the-messages-actively-misled) are corrected**
      in the same change — especially the `fetch_restore` advice, which should be
      conditional on `<original>.tobedeleted` being absent.

## 12. The messages actively misled

| Printed | Reality |
|---|---|
| `⚠️ File busy or locked. Retrying... (1/3)` | The locked file was `.mediavault_txn.json`, not the media file. The message names neither. |
| `❌ Error removing file: [WinError 2] …NAHOM.mkv` | Nothing was being removed. The call that raised was `os.chmod` at `main.py:5378`, and the path was absent because the previous attempt had already renamed it. |
| `❌ IRREVERSIBLE: replace failed after the commit point` | Accurate about the journal, but reads as data loss when the master was intact on disk one filename away. |
| `> To recover the file from the cloud: fetch_restore <id>` | **The expensive one.** 62.6 GB of unnecessary Selenium downloading to recover a file that never left the local volume. See [§2](#2-before-anything-else-do-not-run-fetch_restore). |
| `> The original is no longer in place (C9 stale-sweep recovers it next run)` | True — and it directly contradicts the line printed immediately after it. |

Any fix should carry the failing operation and the actual path into these messages, and
gate the `fetch_restore` advice on the master actually being gone.

---

*Recorded 2026-08-24; deepened 2026-08-24 when the fix was deferred to **IMP-R10**.
Sibling dossier: [`../edge-case-unsplittable-tracks/README.md`](../edge-case-unsplittable-tracks/README.md).*
