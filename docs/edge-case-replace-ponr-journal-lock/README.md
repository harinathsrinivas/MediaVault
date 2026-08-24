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
> ⚠️ **The fix touches change-gated code.** See [§6](#6-proposed-fix--change-gated-do-not-implement-unasked).

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
❌ Auto-Pilot Stopped: mov-kor-2003-ataleoftwosisters archived (original committed) — replace failed past the point-of-no-return: rollback() called after point-of-no-return
```

Both operator-facing messages are wrong about what failed. Nothing was being *removed*,
and the file that was *busy or locked* was not the media file. See [§7](#7-the-messages-actively-misled).

## 2. The smoking gun

The folder was left holding **two** journal files:

| File | Size | Content |
|---|---|---|
| `.mediavault_txn.json` | 276 B | `"crossed_ponr": false` |
| `.mediavault_txn.json.tmp` | 275 B | `"crossed_ponr": true` |

The one-byte difference is exactly `false` → `true`. Both carry the same single record
(`create_file` of `…NAHOM.mkv.dummy_tmp.mkv`).

That orphan `.tmp` is the whole diagnosis. `_flush()` (`main.py:753-761`) persists the
journal as **write-temp → `fsync` → `os.replace`**:

```python
tmp = self.path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f); f.flush(); os.fsync(f.fileno())
os.replace(tmp, self.path)                                   # main.py:761
```

The PONR-marked payload reached the `.tmp` and the `os.replace` onto the live journal
**failed**. The durable write never landed. Meanwhile `mark_point_of_no_return()`
(`main.py:790-792`) sets the in-memory flag *before* flushing:

```python
def mark_point_of_no_return(self):
    self.crossed_ponr = True      # main.py:791 — in-memory, unconditional
    self._flush()                 # main.py:792 — may raise
```

So the process ended with `crossed_ponr = True` in memory and `false` on disk. Likely
lock source: antivirus or the Windows Search Indexer touching the folder immediately
after a 62 GB burst of chunk writes.

## 3. How that becomes a hard fail

The retry loop at `main.py:5289-5319` wraps **chmod + rename + journal-flush** in one
`try`:

```python
for attempt in range(3):                                  # main.py:5292
    try:
        os.chmod(original, stat.S_IWRITE)                 # main.py:5295
        os.rename(original, tobedeleted)                  # main.py:5302  ← PONR seam
        journal.mark_point_of_no_return()                 # main.py:5304
        moved = True
        break
    except PermissionError:                               # main.py:5307
        print(f"     ⚠️ File busy or locked. Retrying... ({attempt + 1}/3)")
        time.sleep(1)
    except Exception as e:                                # main.py:5310
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

The comment at `main.py:5312` — `still pre-PONR — reversible` — is false in this path.
By the time attempt 2 reaches it, the PONR has been crossed in memory.

## 4. Why it was survivable

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

## 5. Recovery procedure (performed, reusable)

The same lock can recur, so this is the drill:

1. **`python main.py recover <id>`** — the on-disk journal still said
   `crossed_ponr: false`, so `recover_journal()` replayed the single `create_file`
   inverse (deleted the orphan `dummy_tmp`) and removed the journal:
   `✅ Recovery complete — pre-command state restored.`
2. **Delete `.mediavault_txn.json.tmp` by hand.** `recover` does not clean it — it is
   residue of the failed `os.replace` and is not a journal `recover_journal()` reads.
3. **`python main.py replace <id>`** — the C9 **stale sweep** (`main.py:5271-5283`) saw
   `.tobedeleted` present with `original` absent, restored the master
   (`⚠️ Recovered interrupted replace: restoring original from …`) and aborted **by
   design**: `❌ replace aborted — original restored. Please retry.`
4. **`python main.py replace <id>`** again — clean run, `✅ Replaced/Archived`.

Verified end state: dummy in place (**9,672 bytes**), entry `status: archived`,
`uploaded: True`, 10 chunks + `mvmeta` sidecar on the device, **62.6 GB reclaimed**
(C: 70.5 GB → 125.6 GB free).

> **The self-healing design worked.** The two-rename pattern, the C9 stale sweep, and
> `recover` together walked a torn commit back to a clean state with zero data loss and
> no manual file surgery beyond deleting one orphan `.tmp`. **The bug is in the retry
> loop's error attribution, not in the rollback architecture.**

## 6. Proposed fix — CHANGE-GATED, do not implement unasked

> ### ⚠️ STOP — this is gated code
>
> [`CLAUDE.md`](../../CLAUDE.md) and [`ROLLBACK_MECHANISM.md` §10](../feature-auto-rollback/ROLLBACK_MECHANISM.md)
> list **"the PONR locations or `mark_point_of_no_return()` placement"** among the things
> that must not be modified without an explicit user decision. Any change here MUST first
> state exactly what differs from the documented behavior and be approved as a decision.
> This section records candidates for that conversation — it is **not** an approved plan.

Candidate shapes:

- Move `mark_point_of_no_return()` **out of the rename retry scope**, so a journal-persist
  failure can never be retried as a rename.
- Narrow `except PermissionError` (`main.py:5307`) to the `os.rename` call alone, so only
  a genuinely blocked rename triggers the retry-and-sleep path.
- Make the retry loop re-check `os.path.exists(original)` at the top of each attempt, so a
  completed rename is never re-attempted.

**Open question for the rollback owner** (deliberately not answered here): should a
failure to *durably persist* the PONR marker be fatal at all, given the rename has already
committed and the C9 stale sweep self-heals the file state on the next run? That is a
semantics question about the journal's durability contract, not a code-shape question, and
it belongs in the rollback decision record rather than in this incident write-up.

## 7. The messages actively misled

| Printed | Reality |
|---|---|
| `⚠️ File busy or locked. Retrying... (1/3)` | The locked file was `.mediavault_txn.json`, not the media file. The message names neither. |
| `❌ Error removing file: [WinError 2] …NAHOM.mkv` | Nothing was being removed. The call that raised was `os.chmod` at `main.py:5295`, and the path was absent because the previous attempt had already renamed it. |
| `❌ IRREVERSIBLE: replace failed after the commit point` | Accurate about the journal, but reads as data loss when the master was intact on disk one filename away. |

Any fix should carry the failing operation and the actual path into these messages.

---

*Recorded 2026-08-24. Sibling dossier: [`../edge-case-unsplittable-tracks/README.md`](../edge-case-unsplittable-tracks/README.md).*
