# IMP-C25 Step 15 — disarm (mutation) probes for every new guard

**disarm 62/62 caught** — the PLAN's 15 guards: 55/55 probes (2–6 per guard) · Step 15's own smoke tests and pins: 7/7.

- **Probed code:** `fc4be59` (branch `tmp/c25_step15`). Step 15 changes tests only, so the product code probed is the
  code of Steps 0–14 (`068212e`, unchanged); the catchers include Step 15's new smoke tests and pins.
- **Isolation:** one uninterrupted run (2026-10-20) of `<scratchpad>\disarm_15.py run` on a fresh `git archive fc4be59`
  copy (`<scratchpad>\disarm_15_copy`); the worktree was only read. The probed files were checked byte-equal to the
  worktree first. The copy alone got one addition: an autouse safety net appended to ITS `tests/conftest.py` that
  refuses any `subprocess.run` of a real `adb` (a probe may switch a device check on; no test may then reach a phone).
- **Method:** each probe = exact text edit(s) (each target must occur exactly once — the script's `check` mode proved
  all 62 against the worktree first) → its catchers run WITHOUT `-x`, so every failing test is listed → every edited
  file restored and compared byte for byte (the run stops otherwise). CAUGHT = at least one catcher fails.
- **Gates of the run:** unmutated copy + the safety net: union of every catcher 1334 passed (112 s) · 62/62 caught, 0
  timeouts, every file restored · the restored copy: 1334 passed again (122 s). Raw results: `<scratchpad>\
  disarm_15_results.jsonl`, `disarm_15_run.log`.
- **Precision pass** (`<scratchpad>\s15_smoke_confirm.py`: the same mutation, ONLY the named node): the new smoke tests
  catch G3b, G3c, G3e, G8a and G11b on their own. G7a was at first caught only by `test_map_photos` — the map smoke's
  single object was mapped, so no object ever needed a phone; the smoke now also holds an episode found in no account
  (d73e636) and catches G7a too (re-confirmed on the copy, together with G3b; the copy's smoke 102 green unmutated).
- **No survivor, so no test was added.** One test-side defect was found and fixed while designing the probes (15.f0,
  e94f495): device_inventory swallows errors, so the smoke's raising adb stand-ins are now recorders asserted empty.
- `<scratchpad>` = `C:\Users\harin\AppData\Local\Temp\claude\C--Users-harin-PycharmProjects-MediaVault\63b4ae7b-212c-4a85-8e4a-1436806bc271\scratchpad`
  (outside the repository). Re-run: `python disarm_15.py check <worktree>` · `python disarm_15.py run <worktree>
  <copy dir> <results.jsonl>` (resumable: finished probes are kept in the results file).

## The guards (PLAN Step 15's list) → code → probe → caught by

Caught-by names the failing tests (file::test — the first ones, then the total). "Earlier" = the same guard's probe in
the step that built it (Steps 2b–14), re-targeted at today's code where it moved; "this step" = that exact mutation is
in none of the earlier probe lists on record here (Steps 2b–8 / 9 / 9b recorded theirs only as summaries).

### G1 — pin rule (P-2): a `gphotos` block is trusted only when its pin is the object's current SHA-256
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G1a | mvcommon.block_trusted | the pin is no longer compared | 5 failed — test_gphotos_primitives::test_block_trusted_is_the_p2_pin_rule, ::test_cloud_state_confirmed_only_from_a_trusted_block, ::test_uploaded_name_precedence_trusted_block_then_capture_then_rule, ::test_apply_conflicts_return_the_block_unchanged[…pin-mismatch] … | Step 3 D1 |
| G1b | mvcommon.block_trusted | the block version is no longer checked | 2 failed — test_gphotos_primitives::test_block_trusted_is_the_p2_pin_rule, ::test_uploaded_name_precedence_… | Step 3 D1b |
| G1c | main._gp_fold_object | the engine folds an untrusted block as if trusted | 1 failed — test_gphotos_apply::test_an_untrusted_block_is_replaced_never_trusted | Step 6 D2 |

### G2 — verification before any trigger (P-5, Goal 2): no new stage downloads an unverified item
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G2a | mainfetch._verdict | any candidate verifies by dedupKey | 1 failed — test_locator::test_nothing_verifiable_previews_todays_queries_and_marks_the_skip | Step 12 C P1 |
| G2b | mainfetch._verdict | any file name verifies | 2 failed — test_locator::test_s2_a_legacy_object_is_pinned_by_its_exact_name_never_by_tile_position, ::test_the_live_wrong_click_case_is_fetched_by_its_exact_name_and_todays_search_never_runs | Step 12 C P2 |
| G2c | mainfetch._shows_item | Shift+D pressed although the page never showed the located item | 1 failed — test_locator::test_shift_d_is_never_pressed_on_a_page_that_does_not_show_the_located_item | Step 12 C P8 |
| G2d | mainfetch._shows_item | the item's record trusted anywhere in a page (a neighbour's page passes) | 1 failed — test_locator::test_shift_d_is_never_pressed_on_a_neighbours_page | Step 12 C P25 |

### G3 — a dry run writes nothing
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G3a | main.gphotos_apply | the engine's dry run saves the library | 2 failed — test_gphotos_apply::test_a_dry_run_reports_the_would_be_diff_and_writes_nothing, ::test_the_engine_never_opens_a_rollback_journal | Step 6 D1 |
| G3b | main.cmd_map_photos | map_photos' dry run applies | 8 failed — test_map_photos::test_a_dry_run_writes_nothing_but_its_run_directory, ::test_resolutions_are_validated_and_previewed_by_a_dry_run … · **the new smoke** TestC25ExactItem::test_map_photos_dry_run_maps_and_writes_only_its_run_directory (precision pass) | Step 9 |
| G3c | main.cmd_check_photos | check_photos' dry run applies | 8 failed — test_check_photos::test_the_engine_checks_every_object_in_its_account_and_writes_only_its_run_files … · **the new smoke** test_check_photos_best_on_the_fake_world_writes_only_its_run (precision pass) | Step 13 P9 |
| G3d | main.cmd_fetch_cleanup | fetch_cleanup's dry run deletes what it lists | 4 failed — test_fetch_hygiene::test_the_fetch_cleanup_dry_run_lists_exactly_the_leftovers_and_writes_nothing, ::test_fetch_cleanup_apply_deletes_exactly_the_dry_run_list, ::test_fetch_cleanup_apply_is_refused_while_a_fetch_holds_the_lock · **the new smoke** test_fetch_cleanup_dry_run_lists_the_leftovers_and_writes_nothing | Step 11 P17 |
| G3e | main.cmd_mvmeta_upgrade (+ _mu_write, _mu_argv_ok) | mvmeta_upgrade's dry run writes the phone (all three layers off) | 10 failed — test_mvmeta_upgrade::test_a_dry_run_cannot_write[…], ::test_a_refused_argv_never_runs, ::test_a_dry_run_ctx_writes_nothing_even_when_asked_to … · **the new smoke** test_mvmeta_upgrade_dry_run_on_the_mock_device_writes_nothing (precision pass) | 13c P15 |

### G4 — never delete a foreign file (one MediaVault did not cause to exist)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G4a | mainfetch._drop_unmatched | the ~/Downloads fallback deletes files that were there before the run | 2 failed — test_fetch_hygiene::test_the_fallback_deletes_only_run_new_files_of_the_entrys_family, ::test_a_pre_existing_copy_of_a_fetched_object_stays_in_downloads | Step 11 P3 |
| G4b | mainfetch._drop_unmatched | the fallback deletes run-new files outside the expected-name family | 1 failed — test_fetch_hygiene::test_the_fallback_deletes_only_run_new_files_of_the_entrys_family | Step 11 P4 |
| G4c | mainfetch._journal_files | the start-up cleanup ignores the run's ~/Downloads snapshot | 3 failed — test_fetch_hygiene::test_a_fallback_runs_leftovers_are_only_its_run_new_family_files, ::test_a_journal_naming_foreign_places_deletes_nothing, ::test_an_interrupted_fallback_run_deletes_only_its_own_family_partials | Step 11 P6 |
| G4d | mainfetch.fetch_cleanup_apply | `--apply` deletes without re-checking each file | 1 failed — test_fetch_hygiene::test_fetch_cleanup_apply_re_checks_every_file_before_deleting | Step 11 P18 |
| G4e | main._mu_argv_ok | mvmeta_upgrade's allowlist lets `mv` replace any phone file | 3 failed — test_mvmeta_upgrade::test_the_own_allowlist_refuses_every_other_shape[argv13/14/15] | 13c P33 |

### G5 — home-account routing (P-4): override > learned home > id prefix
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G5a | mainfetch._object_account | the fetch drops the learned layer (prefix only) | 8 failed — test_fetch_routing::test_locator_from_a_trusted_block_routes_to_its_learned_account, ::test_a_learned_account_moves_an_episode_without_any_override, ::test_a_single_episode_goes_straight_to_its_learned_account, ::test_extras_follow_their_own_learned_account … | Step 10 "learned layer removed" |
| G5b | mainfetch._object_account | the learned account outranks the user's override | 4 failed — test_fetch_routing::test_an_override_beats_the_learned_account_and_has_no_record_there, ::test_an_override_wins_and_warns_only_against_a_verified_account[…] | Step 10 "learned outranks override" |
| G5c | main._mp_route | map_photos / check_photos ignore the learned home | 1 failed — test_check_photos::test_an_object_found_in_another_account_is_recorded_there_and_routed_there_next | this step |

### G6 — freshness gate (24 h, routed account, Step 9b recency)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G6a | main._mp_freshness | an enumeration older than 24 h counts as fresh | 4 failed — test_map_photos::test_a_stale_account_gives_no_verdict_and_names_the_recrawl[old…], ::test_evidence_from_a_stale_inventory_is_refused, ::test_in_no_account_needs_every_crawled_inventory_fresh_and_detailed[stale], ::test_a_stale_account_gets_no_unmatched_report | Step 9 "24 h limit removed" |
| G6b | main._mp_settle | a stale default account still gives a verdict | 2 failed — test_map_photos::test_a_stale_account_gives_no_verdict_and_names_the_recrawl[legacy… / old…] | Step 9 "freshness gate removed" |
| G6c | main._mp_recency | Step 9b recency judged by the id prefix's account, not the routed one | 1 failed — test_map_photos::test_recency_counts_from_the_crawl_of_the_account_it_is_routed_to[movies-possible-loss] | this step |
| G6d | main._mp_recency | map_photos: a recent push is never recent | 7 failed — test_map_photos::test_recency_counts_from_the_crawl_of_the_account_it_is_routed_to[None-not-backed-up-yet], ::test_a_recent_push_under_a_name_another_upload_holds_waits_for_its_backup[…] … | Step 9b |
| G6e | mainfetch._check_recent | the check child looks for a recent push in the other accounts | 1 failed — test_check_photos::test_a_recent_push_that_is_not_backed_up_yet_is_not_looked_for_elsewhere | Step 13 P4 |
| G6f | main._cp_recent | check_photos' verdict: a recent push is never recent | 2 failed — test_check_photos::test_the_report_states_verdicts_per_account_category_and_approach, ::test_a_name_held_by_other_bytes_is_not_backed_up_yet_while_recent_and_a_loss_later | Step 13 P28 |

### G7 — no adb without `--devices` (Step 9b)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G7a | main._mp_compute | map_photos asks the phones without --devices | 10 failed — test_map_photos::test_without_devices_no_phone_is_asked_and_objects_in_no_account_are_possible_losses (precision pass), ::test_recency_counts_from_the_crawl_of_the_account_it_is_routed_to[…], ::test_in_no_account_needs_every_crawled_inventory_fresh_and_detailed[…] … · **the new smoke** test_map_photos_dry_run_maps_and_writes_only_its_run_directory (since d73e636) | Step 9 "adb run needlessly" |
| G7b | main._cp_settle | check_photos asks the phones without --devices | 1 failed — test_check_photos::test_the_report_states_verdicts_per_account_category_and_approach | this step |

### G8 — a capture never confirms (P-11)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G8a | mvcommon._make_object | a push capture makes the object `confirmed` | 12 failed — test_gphotos_primitives::test_drift_pin_cmd_push_whole_file_and_the_capture_it_leaves, ::test_partial_chunk_push_makes_only_its_chunks_unverified … · **the new smoke** test_prep_and_push_capture_the_facts_and_never_confirm (precision pass) | Step 3 D4 |
| G8b | main._gp_plan | the engine builds identity from a capture alone (objects no record names) | 4 failed — test_gphotos_apply::test_capture_facts_are_folded_into_the_blocks_of_the_named_objects, ::test_every_object_kind_over_all_four_prefixes_gets_a_trusted_confirmed_block, ::test_a_second_apply_is_up_to_date_and_writes_nothing, ::test_orphans_are_reported_and_never_applied | Step 6 D3b |

### G9 — the read-only RPC allowlist (P-9)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G9a | gprpc._require_allowed | both allowlist checks off | 25 failed — test_gprpc::test_every_mutating_or_unknown_rpcid_is_refused_before_the_driver_is_touched[every P13 id …] | Step 2b D1 |
| G9b | gprpc.ALLOWED_RPCIDS | a mutating rpcid (XwAOJf, trash) joins the allowlist | 4 failed — test_gprpc::test_the_allowlist_is_exactly_the_read_only_p12_ids, ::test_every_mutating_or_unknown_rpcid_is_refused_…['XwAOJf'], ::test_the_function_that_sends_repeats_the_allowlist_check, ::test_no_token_email_or_url_in_any_output_message_or_traceback | this step |
| G9c | gprpc._exchange | the check at the one place that sends is removed | 1 failed — test_gprpc::test_the_function_that_sends_repeats_the_allowlist_check | Step 2b D2/D3 |

### G10 — the apply engine's fingerprint re-check (P-10, OD-2)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G10a | main.gphotos_apply | no re-check before the save | 3 failed — test_gphotos_apply::test_a_write_between_load_and_save_reloads_and_repatches, ::test_the_fingerprint_hash_catches_a_rewrite_that_keeps_size_and_mtime, ::test_a_library_that_keeps_changing_aborts_after_three_attempts | Step 6 D4 |
| G10b | main._library_fingerprint | the fingerprint without the SHA-256 (size + mtime only) | 1 failed — test_gphotos_apply::test_the_fingerprint_hash_catches_a_rewrite_that_keeps_size_and_mtime | Step 6 D27 |
| G10c | main.gphotos_apply | the fingerprint taken after the load | 3 failed — the three G10a tests | Step 6 D28 |

### G11 — the auto-confirm only after a successful push-family command (P-19)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G11a | main.after_push_command | it confirms before the success check | 46 failed — test_auto_confirm::test_a_command_that_returned_false_gets_nothing_and_nothing_is_read, ::test_a_command_that_returned_a_true_value_runs_the_auto_confirm[…] … | 13b P1 |
| G11b | main.py's CLI dispatcher (push branch) | the hook called BEFORE its command | 18 failed — test_auto_confirm::test_each_push_family_branch_calls_the_hook_once_after_its_command[push-…], ::test_the_push_branchs_extras_only_path_hands_its_result_to_the_hook … · **the new smoke** TestC25PushFamilyHook::test_a_push_family_run_reaches_the_hook_and_spawns_no_child[push] (precision pass) | 13b P2 |
| G11c | main.after_push_command | None counts as success without the end-state check | 22 failed — test_auto_confirm::test_prep_push_rep_succeeded_when_its_entry_is_archived[…], ::test_prep_push_rep_on_an_alias_id_never_succeeds … | 13b P3 |
| G11d | main.cmd_push | the confirm runs inside cmd_push before its journal commit | 1 failed — test_auto_confirm::test_with_or_without_the_hook_a_real_archive_journals_exactly_the_same_and_the_hook_comes_last | 13b P22 |

### G12 — mvmeta sha1 only when proven
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G12a | main._MU_CONTENT_PROOFS (read by _mu_facts) | sha1 / dedup_key from a filename proof (the PLAN's named probe) | 8 failed — test_mvmeta_upgrade::test_a_filename_or_manual_proof_never_gives_a_content_key[filename] … | 13c P1 |
| G12b | main._MU_CONTENT_PROOFS | … from a manual proof | 1 failed — test_mvmeta_upgrade::test_a_filename_or_manual_proof_never_gives_a_content_key[manual] | 13c P2 |

### G13 — a download URL is never persisted or printed (P-20)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G13a | gprpc._wrap_link | a minted link is a plain string (no redacted wrapper) | 5 failed — test_gprpc::test_a_download_url_never_shows_through_print_repr_format_logging_json_or_a_traceback, ::test_no_token_email_or_url_in_any_output_message_or_traceback, ::test_download_url_mints_the_item_info_link_and_it_downloads_the_original … | Step 2b D4 |
| G13b | gprpc.DownloadUrl.__repr__ | the wrapper's repr shows the link | 3 failed — test_gprpc::test_a_download_url_never_shows_through_print_…, ::test_the_download_url_wrapper_is_small_and_explicit, ::test_no_token_email_or_url_in_… | this step |
| G13c | mainfetch._journal_object | the fetch journal keeps a stage's strings | 2 failed — test_fetch_hygiene::test_the_journal_never_holds_a_url, ::test_the_journal_records_each_entrys_folder_names_and_object_states | Step 11 P20 |
| G13d | mainfetch._head_without_cookies | locate --probe-direct keeps the error text (with the link) | 1 failed — test_locator::test_the_cookie_less_head_request_never_returns_or_raises_its_url | Step 12 C P14 |

### G14 — deletions only inside `.mv_incoming` / of run-new expected-family files (P-12)
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G14a | mainfetch._is_incoming_dir | an incoming folder emptied without its marker | 5 failed — test_fetch_hygiene::test_only_a_marked_incoming_folder_may_be_emptied[…nomarker…], ::test_the_next_start_terminates_only_the_recorded_chrome_and_cleans_only_named_marked_folders … | Step 11 P1 |
| G14b | mainfetch._is_incoming_dir | any marked folder emptied, whatever its name | 3 failed — test_fetch_hygiene::test_only_a_marked_incoming_folder_may_be_emptied[restore/incoming / Downloads], ::test_a_journal_naming_foreign_places_deletes_nothing | Step 11 P2 |
| G14c | mainfetch._in_family | the expected-name family widened to the title | 6 failed — test_fetch_hygiene::test_the_expected_name_family[…] | Step 11 P5 |
| G14d | mainfetch._clean_journal_files | a journal's pending files deleted anywhere | 1 failed — test_fetch_hygiene::test_a_journal_naming_foreign_places_deletes_nothing | Step 11 P7 |
| G14e | mainfetch.fetch_cleanup_plan | fetch_cleanup lists unmarked folders' files | 3 failed — test_fetch_hygiene::test_the_fetch_cleanup_dry_run_lists_exactly_the_leftovers_and_writes_nothing, ::test_fetch_cleanup_apply_deletes_exactly_the_dry_run_list, ::test_fetch_cleanup_apply_re_checks_every_file_before_deleting | Step 11 P32 |

### G15 — Chrome terminated only for the journaled pid whose command line names the profile
| Probe | Code | Mutation | Caught by | Earlier |
|---|---|---|---|---|
| G15a | mainfetch._chrome_still_ours | the start-up cleanup ignores the command line | 3 failed — test_fetch_hygiene::test_the_next_start_terminates_only_the_recorded_chrome_and_cleans_only_named_marked_folders, ::test_only_a_chrome_on_exactly_that_profile_is_ours, ::test_a_chrome_that_handed_over_is_tracked_by_its_debug_port_listener | Step 11 P8 |
| G15b | mainfetch._launched_chrome_is_ours | the session close accepts another profile | 4 failed — test_chrome_robustness::test_a_launched_chrome_is_terminated_when_the_table_or_its_handle_proves_it_ours[another-profile / unreadable-command-line], test_fetch_hygiene::test_a_launched_chrome_that_does_not_exit_is_terminated_only_if_it_is_ours[…] | 14.a-2 |
| G15c | mainfetch._terminate_session_chrome | an ATTACHED Chrome terminated at the close | 3 failed — test_fetch_hygiene::test_an_attached_chrome_is_never_terminated_even_after_ctrl_c_or_by_the_next_start, test_chrome_robustness::test_a_chrome_the_run_only_attached_to_is_never_terminated_even_when_the_table_cannot_tell[attached / unknown] | Step 11 P10 |
| G15d | mainfetch._startup_cleanup | the start-up cleanup terminates an attached Chrome | 1 failed — test_fetch_hygiene::test_an_attached_chrome_is_never_terminated_even_after_ctrl_c_or_by_the_next_start | Step 11 P11 |

## Step 15's own additions bite (S)
| Probe | Code | Mutation | Caught by |
|---|---|---|---|
| S1 | main.cmd_dispatch_fetch | the post-fetch merge of the learned sidecar removed | 4 failed — **smoke** test_fetch_merges_a_seeded_learned_sidecar_into_the_library, test_gphotos_apply::test_dispatch_fetch_merges_what_the_child_learned_after_it_exits, ::test_a_spawn_failure_still_merges_pending_records, ::test_fetch_restore_merges_before_its_restore_phase |
| S2 | mainfetch.fetch_single_entry | a NEW line carries a P-13 web marker | 1 failed — **pins** test_the_p13_markers_are_printed_only_where_they_always_were[mainfetch.py] |
| S3 | mvcommon.calculate_file_hash | a read error raises instead of returning None | 1 failed — **pins** test_calculate_file_hash_returns_none_and_never_raises_when_the_file_cannot_be_read |
| S4 | main.write_remote_mvmeta | the sidecar's byte form changes (indent 4) | 47 failed — **pins** test_the_mvmeta_writer_keeps_the_v1_key_order_and_its_exact_byte_form[whole / split], test_mvmeta_upgrade::test_the_real_writer_s_bytes_decode_and_re_encode_exactly, ::test_a_sidecar_not_in_the_writer_s_exact_form_is_refused[…] … |
| S5 | main.cmd_fetch_restore | fetch_restore spawns its child without the tempdir | 1 failed — **pins** test_fetch_restore_spawns_exactly_the_fetch_child_argv[extras-tempdir] |
| S6 | mainfetch._finish_downloads | the entry's incoming folder is not removed | 12 failed — **smoke** test_a_fetch_leaves_no_incoming_folder_and_never_touches_downloads, test_fetch_hygiene::test_a_download_lands_in_the_entrys_incoming_folder_and_is_renamed_into_restore … |
| S7 | main.cmd_resolve_photos | resolve_photos without its terminal check | 2 failed — **smoke** test_resolve_photos_refuses_without_a_terminal, test_resolve_photos::test_a_pipe_is_refused_before_anything_runs |

## Notes
- Two guards of the PLAN's list overlap; they are probed by what each protects (STATUS.md design decision 6):
  G4 "never-delete-foreign" = a file MediaVault did not cause to exist (pre-existing / non-family files, files changed
  since fetch_cleanup listed them, other phone files); G14 = the delete-scope predicate itself (`_is_incoming_dir`,
  the family rule, pending deletes).
- G7: the copy's adb safety net (see Isolation) means that, while G7a / G7b were applied, a test that reached `adb`
  failed through the net (device_inventory swallows the refusal and reports the phone unreadable — the test's verdict
  assertions then fail). The dedicated catchers fail on their own: test_map_photos' Step 9b test records the runner's
  argv list, and the new smoke records every `subprocess.run` call.
- Probes first written in this step (G5c, G6c, G7b, G9b, G13b) were each caught by an EXISTING test — no test had to
  be added for any guard.
