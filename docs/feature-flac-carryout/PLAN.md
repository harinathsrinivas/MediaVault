# Feature — FLAC carry-out (separate the unsplittable track, store it, re-merge it on restore)

Suggested branch: feature/flac-carryout  (new branch from `main`, NOT merged — user tests manually first)

## Context

`mkvmerge` cannot `--split` a file carrying a `A_FLAC` audio track (packetizer-level
refusal — all six split modes, no flag, through v97). Until now MediaVault's only answer
was **refuse + hand-operator-a-runbook** (`refuse_if_unsplittable`, IMP-C20) and a
separate manual `tools/remux_unsplittable.py` (IMP-C21). The user's new requirement is
that `prep_push_rep_enrich` **just works** on such a file *without converting or dropping
the FLAC*, preserving the exact audio bytes.

The chosen approach (validated this session, 2026-08/09, on the real 53.35 GB
"Black Panther: Wakanda Forever" REMUX whose 8ch/7.1 English FLAC is track 2):

1. **Extract** the FLAC track (stream-copy → a standalone `.flac`, byte-exact payload).
2. **Wrap it** in a tiny valid playable MKV as a **Matroska attachment** — Google Photos
   ingests it as a normal video and returns it **byte-identical** (proven: holder vs
   downloaded SHA256 `17CC9931…` identical). This is the same "dummy video" idea,
   generalized so the payload underneath is opaque bytes (FLAC today, ISO later).
3. **Split** the movie **with the FLAC dropped** (one `mkvmerge --split --audio-tracks !N`
   pass) → normal ~10 GB chunks.
4. **Restore**: fetch chunks + holder → extract the FLAC from the holder → re-add it as a
   track in the **same deterministic merge** (`mkvmerge --deterministic <seed>`).
5. **Determinism holds**: the deterministic merge *with the FLAC re-added* reproduces
   byte-identically (proven: two merges → identical SHA256 `ffe1b8d3…`, same size, FLAC
   re-added as track 4). So the existing h1 → split → h2-bless → compare-against-h2 model
   is unchanged.

This reverses the 2026-08-25 "never auto-convert" rule **in a way that converts nothing**:
the FLAC codec is untouched; only its container journey changes. If and only if a **FLAC
track is present AND a split is requested** does the carry-out engage. A small FLAC file
pushed **without** split (e.g. an 8 GB movie) uploads whole and is untouched.

## Goal

`prep_push_rep_enrich` (and `push` on a prepped entry) archives a FLAC-bearing file when a
**split is requested**, by carrying the FLAC out into a valid-video "holder", splitting
the remainder, and recording the carry-out in `split_info`; `fetch_restore`/`restore`
recover the byte-exact FLAC and re-add it during the deterministic merge, reproducing the
canonical hash. No codec conversion, no track dropped, deterministic re-hash contract
preserved. When FLAC is present but **no split** is requested, behavior is byte-for-byte
unchanged (whole-file upload).

## Files affected

- `main.py` — the carry-out (extract + wrap + split-drop) in `cmd_push`; the re-add in
  `cmd_restore`; extend `split_video_file` and `merge_video_files`; new generic
  `wrap_payload_in_container` + `extract_payload_from_container` + `find_unsplittable_tracks`
  already returns the FLAC track id; new `_flac_holder_name` helper.
- `mainfetch.py` — `fetch_single_entry` / `build_download_queue` queue the holder as one
  more file (hash-routed like a chunk) into `restore/`.
- `tests/` — new `tests/test_flac_carryout.py` + smoke additions; extend
  `tests/test_unsplittable_preflight.py` (registry unchanged — FLAC still unsplittable;
  only the *handling* changes).
- `ARCHITECTURE.md`, `README.md` — document the carry-out lifecycle + the new `split_info`
  field + the two new commands/flags.
- `docs/improvements/improvements_tier*.md` / `PRIORITY.md` — register **IMP (ISO / opaque
  payload generalization)** as a separate future task.
- `docs/feature-flac-carryout/DECISIONS.md` — decision record (this dir).

## Approach

The carry-out is deliberately **orthogonal** to the existing split: it runs only inside the
`should_split` branch of `cmd_push`, directly where `refuse_if_unsplittable` already fires.
Instead of refusing, we *carry out*:

- **Push leg** (`cmd_push`, after the free-space + unsplittable checks):

  1. `find_unsplittable_tracks(local_file_path)` already yields the FLAC track id (`N`).
     When the only unsplittable codec is FLAC, extract it with
     `ffmpeg -map 0:N -c copy <tmp>.flac`.
  2. Record the FLAC's **track metadata** BEFORE extraction: its position in the original
     track list, and `{language, default, forced, enabled, name}` (from `mkvmerge -J`,
     matched by its stable track UID). This is what restores exact order + flags (D-9).
  3. Wrap: `wrap_payload_in_container(<tmp>.flac, <parts_dir>/<name> [<short_id>].holder.mkv)`
     → 10 s H.264+AAC stub with the `.flac` as a Matroska attachment.
  4. Verify the wrap round-trips (extract-and-md5 = source) before uploading.
  5. Split the *source* with the FLAC dropped:
     `split_video_file(..., drop_track=N)` → `mkvmerge -o … --split … --audio-tracks !N`.
  6. Hash the holder; record it in `split_info.carried_out_tracks` (position + flags +
     holder filename/hash; see schema). Journal the holder as `record_create_file`
     (created-this-run) so a pre-upload rollback removes it.
  6. Upload the holder **alongside** the chunks (same `files_to_upload_paths` loop, same
     `.partial`→`mv` reliability path). The holder is just one more ~size(N) file, typically
     well under the 10 GB Photos cap.

- **Restore leg** (`cmd_restore`, split branch):

  - The holder sits in `restore/` next to the chunks (fetch queues it; see fetch below).
  - Before the merge, verify the holder hash, then `extract_payload_from_container` →
    `<tmp>.flac` (byte-exact, proven).
  - Rebuild the FLAC into the deterministic merge **at its original position with its
    original flags** (D-9): `merge_video_files(chunk_paths, out, seed, carried_track=...)`
    emits `--track-order 0:0,…,N:0,…,0:1` and the FLAC's per-file `--language`/
    `--default-track`/`--forced-track`/`--track-name` options, appending the extracted
    `.flac` as an **additional (non-concatenated) input**. This is what keeps the FLAC in
    the middle, not appended last, and it does NOT reorder the cover art.
  - Everything else (verify-or-bless, bless-at-first-restore / verify-after, PONR, chunk
    delete) is unchanged. The deterministic merge now *includes* the re-added FLAC, so the
    blessed h2 already accounts for it.

- **Fetch leg** (`mainfetch.py`):

  - `build_download_queue` and `fetch_single_entry` gain one line each: when
    `split_info.carried_out_tracks` exists, enqueue each `holder_filename` + `holder_hash`
    as a pending file (same hash-routing, `restore/` dest). No new download mechanism.

- **Generic container helpers** (the ISO-reuse requirement):

  - `wrap_payload_in_container(payload, out, …)`: build a tiny H.264/AAC stub and attach
    `payload` via `mkvmerge --attach-file`. Shared by the FLAC carry-out now and ISO later.
  - `extract_payload_from_container(container, out)`: `mkvextract attachments <id>:<out>`.
  - Both are payload-agnostic (bytes in → bytes out) and unit-tested without real media.

## Steps

- [ ] 1. [model: opus] [effort: high] Generic container helpers + `find_unsplittable_tracks` wiring.
  - Files: `main.py`
  - Details: add `wrap_payload_in_container(payload_path, out_path)` (build a **10 s**
    testsrc H.264+AAC stub with `ffmpeg` — the same clip the user already uploaded to
    Photos successfully — then `mkvmerge --attachment-name <basename> --attach-file
    <payload> <stub>`); add `extract_payload_from_container(container_path, out_path)` (probe
    `-J` for the attachment id, `mkvextract attachments <id>:<out>`). Return bytes-exact
    results; verify with an md5 compare helper. No change to `find_unsplittable_tracks` yet
    (it already returns the FLAC id).
  - Acceptance: a unit test wraps a byte blob, extracts it, and asserts md5 equality, using
    a sandbox temp dir (never real C:\Media).

- [ ] 2. [model: opus] [effort: high] `split_video_file` drop-track + generic `merge_video_files` manifest-driven re-add.
  - Files: `main.py`
  - Details: `split_video_file(input, out, method, value, file_id="", drop_track=None)` —
    when `drop_track` is an int, append `--audio-tracks !<N>` to the mkvmerge argv (before
    the input path); default `None` keeps argv byte-for-byte today. `merge_video_files
    (chunk_paths, output_path, seed=None, carried=None)` — when `carried` is a
    `carried_out_tracks` record (position + flags + `original_tracks` manifest), REGENERATE
    the argv **from the manifest** (never hardcode): emit `--track-order` computed so the
    merged track list is identical in order+UID to `original_tracks`, and re-apply the FLAC's
    (and any perturbed track's) `--language`/`--default-track`/`--forced-track`/
    `--track-name`/`--enabled-track` options placed immediately before the `.flac` input
    (FID = len(chunks)). Matched by UID, not by sample-specific indices — see DECISIONS.md
    D-9.
  - Acceptance: existing split/merge tests pass unchanged; a new test with a DIFFERENT track
    set (e.g. FLAC as default, a forced sub, a named track) asserts the argv reconstructs
    that exact layout — proving genericity, not one sample.

- [ ] 3. [model: opus] [effort: high] Carry-out + holder in `cmd_push`.
  - Files: `main.py`
  - Details: in the `should_split` branch, after `refuse_if_unsplittable` — REPLACE the
    blanket refusal with: if the unsplittable list is exactly `A_FLAC` track(s), extract +
    wrap + split-drop; otherwise still refuse (unknown unsplittable codec). Journal the
    holder (`record_create_file`), hash it, and write
    `library[id]["split_info"]["carried_out_tracks"] = [{track_id, codec, language,
    channels, position, default, forced, enabled, name, holder_filename, holder_hash,
    original_tracks}]` where `original_tracks` is the full ordered per-track manifest of the
    source (each `{type, codec_id, language, default, forced, enabled, name, uid}`) — the
    generic reconstruction source (D-9/D-6). A FLAC track with **no** split request never
    reaches this branch (whole-file upload, unchanged). Keep the eager-rehash and tempdir
    behavior working: holder lives under `base_dir` next to `_parts/` chunks so `tempdir`
    continues to relocate it.
  - Acceptance: a mock-device test archives a FLAC fixture (drop-track split + holder
    uploaded + `carried_out_tracks` incl. `original_tracks` recorded); a no-split FLAC test
    bypasses carry-out.

- [ ] 4. [model: opus] [effort: high] Fetch queue + restore re-add.
  - Files: `mainfetch.py`, `main.py`
  - Details: `build_download_queue`/`fetch_single_entry` enqueue each holder (filename +
    hash → `restore/`). `cmd_restore` split branch: verify holder hash, extract FLAC to a
    temp inside `restore/` (reproducible — journalled like the merge temp), call
    `merge_video_files(chunk_paths, merge_tmp, seed, carried=…)` (manifest-driven re-add,
    per step 2/D-9), then proceed with the existing verify-or-bless exactly as today.
  - Acceptance: a staged restore with a holder reproduces the FLAC (track re-added at its
    position with its flags) and the deterministic hash matches across two merges (the H2
    proof, in-test via a tiny fixture).

- [ ] 5. [model: sonnet] [effort: medium] Schema guard + smoke + Consumer Impact tests.
  - Files: `tests/test_flac_carryout.py`, `tests/smoke/test_smoke_all_commands.py`,
    `tests/test_entry_schema_guard.py`
  - Details: add a split_info `carried_out_tracks` shape assertion to the schema guard; add a
    smoke case (a split entry with `carried_out_tracks` flows through fetch→restore without
    breaking the guard/test). Never touch real C:\Media or real library_*.json; run
    `pytest -q` and `pytest tests/smoke -q`.
  - Acceptance: full suite + smoke green.

- [ ] 6. [model: haiku] [effort: low] Docs: README + ARCHITECTURE + IMP registration.
  - Files: `README.md`, `ARCHITECTURE.md`, `docs/improvements/*`, `PRIORITY.md`
  - Details: document the FLAC carry-out lifecycle, the `split_info.carried_out_tracks`
    schema, the "holder" convention (`[<short_id>].holder.mkv` beside chunks), and the
    generic-payload reuse. Register **IMP — containerize arbitrary payloads (BluRay .iso)**
    as a separate future task reusing `wrap_payload_in_container`.
  - Acceptance: docs accurate against final code; IMP tracked, not done.

## Risks and edge cases

- **Google Photos acceptance** — de-risked live: the holder uploads and comes back
  byte-identical (user confirmed). The remaining real-world risk is *indexing* of a short
  video + large attachment across many titles; mitigated by the H.264/AAC stub being a
  genuine playable video (same trick the 10 KB dummy already relies on).
- **Change-gate** (CLAUDE.md / ROLLBACK_MECHANISM.md §10): **no PONR moves, no journal
  format change, no `recover_journal` semantics change.** The holder is a NEW
  `record_create_file` (created-this-run), which is the same pre-authorized pattern as
  `checksums/`/`_parts/`. The `split_info` change is ADDITIVE (new optional field) and rides
  the already-journalled `record_set_field("split_info")` — a push rollback reverts the
  whole dict including the new field. Therefore this is change-gate-SAFE, but it is
  documented in DECISIONS.md for the record.
- **Deterministic merge with the re-added FLAC** — proven reproducible at slice scale. The
  FLAC re-add happens INSIDE the `--deterministic` run (not a second non-deterministic
  pass), which is what keeps h2 stable.
- **More than one unsplittable track / non-FLAC unsplittable codec** — still refuses
  (unchanged). Only the single known-FLAC case is automated.
- **matroska cover-art attachment** — the source already carries an MJPEG cover (track 9).
  The holder's own attachment is separate and correctly re-derived; no collision.
- **2X disk at restore** — unchanged (merge already needs chunks + output). The holder
  adds its own ~1×FLAC-size to the fetch side only, which is small.

## Consumer Impact Analysis

The change is **additive** (`split_info.carried_out_tracks`), so most `split_info` consumers
are unaffected; those that iterate `chunks` ignore the new key. The consumers that MUST
change:

| # | Consumer | Access | Verdict | Why |
|---|---|---|---|---|
| 1 | `cmd_restore` (split merge) | `entry["split_info"]["chunks"]`, then `merge_video_files` | **needs-fix** | must extract holder + pass FLAC as extra_input (step 4) |
| 2 | `mainfetch.build_download_queue` | enumerates `split_info["chunks"]` | **needs-fix** | must also enqueue `carried_out_tracks[*].holder_filename/hash` (step 4) |
| 3 | `mainfetch.fetch_single_entry` | same chunk enumeration | **needs-fix** | same queue addition (step 4) |
| 4 | `_chunk_hashes` in `cmd_push` | sourced from `chunk_metadata` / `split_info["chunks"]` | safe | holder is in `carried_out_tracks`, not `chunks`; not mixed |
| 5 | `test_entry_schema_guard` | asserts split_info shape | **needs-fix** | add carried_out_tracks to the guarded shape (step 5) |
| 6 | `cmd_check` / `verify_library` / `web` / extras | read `split_info.is_split/chunks/method` | safe | `.get()`/iteration; unknown key ignored |
| 7 | rollback (schedule/rollback) | `record_set_field("split_info")` whole-dict | safe | whole dict reverts incl. new field |

No other consumers dereference a key this feature changes (it adds one key, changes none).

## Verification

1. `python -m pytest tests/test_flac_carryout.py tests/test_unsplittable_preflight.py -q`
2. `python -m pytest -q`
3. `python -m pytest tests/smoke -q` — **mandatory SMOKE-GATE (main.py/mainfetch.py touched), final gate.**

## Out of scope

- **ISO / arbitrary-payload archival** (the BluRay `.iso`→container feature) — registered as
  a separate IMP; `wrap_payload_in_container` is the shared primitive, but no ISO wiring here.
- **Multi-track / non-FLAC unsplittable codecs** — still refuse-with-runbook.
- **Lossy conversion / track dropping** — never automated (standing decision remains: the
  exact bytes are preserved, never degraded).
- **`tools/remux_unsplittable.py`** — left as-is (still the manual path for cases this
  feature does not automate); it is NOT wired into the pipeline.
- **Change-gated rollback internals** — untouched (PONRs, journal format, recover semantics).
