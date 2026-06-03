# IMP-R2 Decisions

## D-1: Wrapper-only — change-gate honored
`cmd_recover` is a thin CLI wrapper; `recover_journal` (main.py:561-592) was not modified. The function's semantics, journal format/durability (fsync + os.replace), PONR logic, and `_replay_inverses` behavior are all unchanged. This satisfies the CLAUDE.md change-gate for the auto-rollback mechanism.

## D-2: Id-vs-path resolution rule
Resolution checks the merged library first (`target in library and entry["folder_path"]`); if not found, treats the argument as a literal folder path. Library keys always take precedence. A slug like `mov-…` that is also a real folder name (vanishingly unlikely) would resolve via the library — this is the documented and intended precedence.

## D-3: `--scan` is read-only
`cmd_recover(scan=True)` never calls `recover_journal`. It only reads journals (`json.load`) and reports their state. This satisfies the IMP-R5 read-only sweep contract and ensures `recover --scan` cannot trigger destructive recovery unexpectedly.

## D-4: Dispatch joins `sys.argv[2:]`
The `recover` dispatch branch uses `" ".join(sys.argv[2:])` (matching the `prep`/`prep_season` convention) so folder paths containing spaces are handled correctly without shell quoting gymnastics.
