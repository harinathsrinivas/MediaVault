/* Regression guard for the MediaVault web console data model (IMP-E14 polish).
 *
 * THE BUG THIS PINS:
 *   An ARCHIVED title (a tiny on-disk dummy; library status=archived, uploaded=
 *   True) was appearing under the "Unprepped" AND "Local · not pushed" sub-views
 *   in the web UI, even though each leaf card's badge correctly read "Archived".
 *   The cause was a CSS gap that left the per-state rail visible in GROUPED mode
 *   (fixed in styles.css). This test guards the DATA half of the contract: the
 *   pure merge in data.js (loadModel + countBy) must assign EXACTLY ONE state to
 *   each id and never let an ARCHIVED row leak into the UNPREPPED / LOCAL bucket.
 *
 * WHY A NODE TEST (no framework):
 *   data.js is a DOM-free ES module. `loadModel()` is the pure merge of /api/items
 *   + /api/reclaim and is exercisable in isolation by stubbing the global `fetch`.
 *   The repo has no JS test harness yet, so this file self-runs and exits non-zero
 *   on the first failed assertion. Run directly:  node tests/js/test_data_buckets.mjs
 *   (a pytest wrapper, tests/test_web_data_union_js.py, shells to it in CI).
 *
 * IMPORTANT: imports the SAME webui/static/data.js the browser ships — no copy —
 * so a future regression in the real merge fails this test.
 */
"use strict";

import { fileURLToPath } from "node:url";
import * as path from "node:path";

// Resolve webui/static/data.js relative to THIS file (tests/js/ -> ../../webui).
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DATA_JS = path.resolve(__dirname, "..", "..", "webui", "static", "data.js");

// ---- tiny assert helpers (exit non-zero on failure) -----------------------
let failures = 0;
function ok(cond, msg) {
  if (cond) {
    console.log("  PASS: " + msg);
  } else {
    failures += 1;
    console.error("  FAIL: " + msg);
  }
}
function eq(actual, expected, msg) {
  ok(actual === expected, msg + " (got " + JSON.stringify(actual) + ", want " + JSON.stringify(expected) + ")");
}

// ---- fixed payloads modeling the real bug scenario ------------------------
//
// /api/items is the authoritative library-centric feed. It includes the ARCHIVED
// dummy (mov-hi-2023-12thfail) and a chernobyl episode (tv-en-2019-chernobyl-e01),
// PLUS a genuine LOCAL_NOT_PUSHED movie. The season_map parent is correctly absent
// (items_payload never emits season_map / multi_ep_alias rows).
const ITEMS_PAYLOAD = {
  items: [
    {
      id: "mov-hi-2023-12thfail",
      category: "movies",
      state: "ARCHIVED",
      size_bytes: 9672,
      path: "C:/Media/Movies/Hindi/12th Fail (2023)/12thfail.mkv",
      title: "12th Fail",
      year: 2023,
      tmdb_id: 123456,
      poster_available: true,
      chunk_count: 1,
      parent_id: null,
    },
    {
      id: "tv-en-2019-chernobyl-e01",
      category: "series",
      state: "ARCHIVED",
      size_bytes: 9672,
      path: "C:/Media/Series/English/Classic/Chernobyl/S01/e01.mkv",
      title: "Chernobyl E01",
      year: 2019,
      tmdb_id: 7000,
      poster_available: true,
      chunk_count: 1,
      parent_id: "tv-en-2019-chernobyl",
    },
    {
      id: "mov-en-2024-realmovie",
      category: "movies",
      state: "LOCAL_NOT_PUSHED",
      size_bytes: 2_000_000_000,
      path: "C:/Media/Movies/English/RealMovie (2024)/realmovie.mkv",
      title: "Real Movie",
      year: 2024,
      tmdb_id: 555,
      poster_available: false,
      chunk_count: 1,
      parent_id: null,
    },
  ],
};

// /api/reclaim carries UNPREPPED rows + enrichment. It does NOT carry the archived
// dummy as UNPREPPED (the backend was verified to exclude archived/season_map).
// It DOES enrich the genuine LOCAL movie (same id) and carries one real UNPREPPED
// sample file (a different id, not in /api/items) — the only legit UNPREPPED row.
const RECLAIM_PAYLOAD = {
  total_reclaimable_bytes: 2_300_000_000,
  total_reclaimable_human: "2.14 GB",
  items: [
    {
      id: "mov-en-2024-realmovie",
      badge: "LOCAL_NOT_PUSHED",
      path: "C:/Media/Movies/English/RealMovie (2024)/realmovie.mkv",
      size_bytes: 2_000_000_000,
      suggested_command: "python main.py push mov-en-2024-realmovie SIZE_GB 8",
      suggested_folder: null,
      guessed: false,
    },
    {
      id: "mov-en-0000-sample",
      badge: "UNPREPPED",
      path: "C:/Media/Movies/English/RealMovie (2024)/Sample/Sample.mkv",
      size_bytes: 300_000_000,
      suggested_command: 'python main.py prep mov-en-0000-sample "C:/.../Sample/Sample.mkv"',
      suggested_folder: { applies: true, folder: "Movies/English/Sample {tmdb-?}" },
      guessed: true,
    },
  ],
};

// Stub the global fetch loadModel() depends on. Returns the right payload per URL.
globalThis.fetch = function (url) {
  let body;
  if (url === "/api/items") body = ITEMS_PAYLOAD;
  else if (url === "/api/reclaim") body = RECLAIM_PAYLOAD;
  else return Promise.reject(new Error("unexpected fetch url: " + url));
  return Promise.resolve({
    ok: true,
    status: 200,
    json: function () {
      return Promise.resolve(body);
    },
  });
};

// ---- run the assertions ----------------------------------------------------
// import() needs a file:// URL for an absolute path on Windows; build it from the
// resolved data.js path so we load the REAL shipped module (not a copy).
const DATA_JS_URL = new URL("file:///" + DATA_JS.replace(/\\/g, "/"));

async function main() {
  const mod = await import(DATA_JS_URL);
  const model = await mod.loadModel();

  console.log("Scenario: ARCHIVED dummy + chernobyl episode + 1 real LOCAL + 1 real UNPREPPED");

  // (a) The archived dummy appears EXACTLY ONCE in the merged model.
  const dummyRows = model.items.filter((r) => r.id === "mov-hi-2023-12thfail");
  eq(dummyRows.length, 1, "archived dummy 'mov-hi-2023-12thfail' appears exactly once");

  // (b) Its single row's state is ARCHIVED (never rewritten to UNPREPPED/LOCAL).
  if (dummyRows.length === 1) {
    eq(dummyRows[0].state, "ARCHIVED", "archived dummy row state === ARCHIVED");
  }

  // (c) Per-(category,state) counts: the dummy is counted under ARCHIVED, and the
  //     movies UNPREPPED bucket counts ONLY the genuine sample (not the dummy).
  const movies = model.counts.byCatState.movies || {};
  eq(movies.ARCHIVED, 1, "counts.byCatState.movies.ARCHIVED === 1 (the dummy)");
  eq(movies.UNPREPPED, 1, "counts.byCatState.movies.UNPREPPED === 1 (only the real sample)");
  eq(movies.LOCAL_NOT_PUSHED, 1, "counts.byCatState.movies.LOCAL_NOT_PUSHED === 1 (the real movie)");

  // The chernobyl episode counts under series ARCHIVED, nothing else.
  const series = model.counts.byCatState.series || {};
  eq(series.ARCHIVED, 1, "counts.byCatState.series.ARCHIVED === 1 (chernobyl episode)");
  ok(!series.UNPREPPED, "counts.byCatState.series.UNPREPPED is absent/0 (chernobyl NOT unprepped)");

  // (d) THE CORE INVARIANT: no id appears in more than one (category,state) bucket.
  //     Build id -> Set(state) over the merged model; every id must map to one state.
  const idStates = new Map();
  model.items.forEach((r) => {
    if (!idStates.has(r.id)) idStates.set(r.id, new Set());
    idStates.get(r.id).add(r.state);
  });
  let multiBucket = [];
  idStates.forEach((states, id) => {
    if (states.size !== 1) multiBucket.push(id + " -> {" + [...states].join(",") + "}");
  });
  ok(
    multiBucket.length === 0,
    "every id maps to exactly ONE state" +
      (multiBucket.length ? " (offenders: " + multiBucket.join("; ") + ")" : "")
  );

  // (e) Cross-check: an UNPREPPED reclaim row and an ARCHIVED items row for the
  //     SAME physical FILE but DIFFERENT ids must not collapse into one title in
  //     one bucket. Here the dummy (archived, .../12thfail.mkv) and the sample
  //     (unprepped, .../Sample/Sample.mkv) are distinct ids -> distinct rows, and
  //     neither id leaks into the other's state. (Verified by (a)-(d) above; this
  //     asserts the sample's own row is UNPREPPED and unique.)
  const sampleRows = model.items.filter((r) => r.id === "mov-en-0000-sample");
  eq(sampleRows.length, 1, "real UNPREPPED sample appears exactly once");
  if (sampleRows.length === 1) {
    eq(sampleRows[0].state, "UNPREPPED", "real sample row state === UNPREPPED");
  }

  if (failures > 0) {
    console.error("\n" + failures + " assertion(s) FAILED");
    process.exit(1);
  }
  console.log("\nAll data-bucket assertions passed.");
}

main().catch((err) => {
  console.error("test crashed: " + (err && err.stack ? err.stack : err));
  process.exit(1);
});
