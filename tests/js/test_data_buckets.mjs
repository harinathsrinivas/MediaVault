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

// Resolve webui/static/{data,tree}.js relative to THIS file (tests/js/ -> ../../webui).
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DATA_JS = path.resolve(__dirname, "..", "..", "webui", "static", "data.js");
const TREE_JS = path.resolve(__dirname, "..", "..", "webui", "static", "tree.js");

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
// resolved paths so we load the REAL shipped modules (not copies). tree.js is
// DOM-free at module load (all DOM access is deferred into functions), so the pure
// prune logic imports cleanly under node — same discipline as data.js.
const DATA_JS_URL = new URL("file:///" + DATA_JS.replace(/\\/g, "/"));
const TREE_JS_URL = new URL("file:///" + TREE_JS.replace(/\\/g, "/"));

// ---------------------------------------------------------------------------
// Scenario 2 — the GROUPED-view state PRUNE (pruneTreeByState in tree.js).
//
// THE BEHAVIOR THIS PINS: in grouped mode, selecting a state filters the folder
// tree. The rule (user-explicit): keep a LEAF only if its effective state matches;
// keep a FOLDER only if some descendant leaf (at ANY depth) matches — folders with
// no matching descendant are DROPPED. "English" shows under "Unprepped" iff some
// leaf under English is actually unprepped. "All" keeps everything untouched, and
// the prune must NOT mutate the input tree (the cached /api/tree is reused).
//
// Fake tree (mirrors /api/tree's roots[category] shape):
//   Mixed/        (folder)
//     ├─ Sub/     (folder, nested — proves the "any depth" rule)
//     │    └─ leaf u1  UNPREPPED (size 100)
//     └─ leaf a1       ARCHIVED  (size 9000)   ← model row overrides leaf.state
//   AllArchived/  (folder)
//     ├─ leaf a2       ARCHIVED  (size 8000)
//     └─ leaf a3       ARCHIVED  (size 7000)
// The model row for a1 (id "x-a1") carries state ARCHIVED, exercising the JOIN in
// effectiveLeafState (model row wins over a deliberately WRONG leaf.state).
function fakeTree() {
  return [
    {
      type: "folder",
      name: "Mixed",
      path: "/m/Mixed",
      has_image: false,
      size_bytes: 9100,
      children: [
        {
          type: "folder",
          name: "Sub",
          path: "/m/Mixed/Sub",
          has_image: false,
          size_bytes: 100,
          children: [
            { type: "leaf", id: "x-u1", state: "UNPREPPED", size_bytes: 100, title: "U1" },
          ],
        },
        // NOTE: leaf.state is deliberately WRONG ("UNPREPPED") to prove the model
        // row (ARCHIVED) wins in effectiveLeafState — the prune must agree with the
        // badge, which reads the model row.
        { type: "leaf", id: "x-a1", state: "UNPREPPED", size_bytes: 9000, title: "A1" },
      ],
    },
    {
      type: "folder",
      name: "AllArchived",
      path: "/m/AllArchived",
      has_image: false,
      size_bytes: 15000,
      children: [
        { type: "leaf", id: "x-a2", state: "ARCHIVED", size_bytes: 8000, title: "A2" },
        { type: "leaf", id: "x-a3", state: "ARCHIVED", size_bytes: 7000, title: "A3" },
      ],
    },
  ];
}

const PRUNE_MODEL_BY_ID = {
  // a1's authoritative state is ARCHIVED (overrides the wrong leaf.state above).
  "x-a1": { id: "x-a1", state: "ARCHIVED" },
  // a2/a3/u1 omitted on purpose → effectiveLeafState falls back to leaf.state.
};

function leafIds(nodes) {
  var out = [];
  (nodes || []).forEach(function walk(n) {
    if (n && n.type === "leaf") out.push(n.id);
    else if (n && n.children) n.children.forEach(walk);
  });
  return out;
}

function findFolder(nodes, name) {
  var hit = null;
  (nodes || []).forEach(function walk(n) {
    if (!n || hit) return;
    if (n.type === "folder" && n.name === name) hit = n;
    if (n.children) n.children.forEach(walk);
  });
  return hit;
}

async function runPruneScenario(tree) {
  const mod = await import(TREE_JS_URL);
  const { pruneTreeByState, effectiveLeafState } = mod;

  console.log("\nScenario 2: grouped-view state PRUNE (pruneTreeByState)");

  // effectiveLeafState: the model row wins over a wrong leaf.state (badge parity).
  eq(
    effectiveLeafState({ id: "x-a1", state: "UNPREPPED" }, PRUNE_MODEL_BY_ID),
    "ARCHIVED",
    "effectiveLeafState prefers the model row's state (ARCHIVED) over leaf.state"
  );
  eq(
    effectiveLeafState({ id: "x-u1", state: "UNPREPPED" }, PRUNE_MODEL_BY_ID),
    "UNPREPPED",
    "effectiveLeafState falls back to leaf.state when no model row"
  );

  // --- prune to UNPREPPED ---------------------------------------------------
  const unprepped = pruneTreeByState(tree, "UNPREPPED", PRUNE_MODEL_BY_ID);

  // The Mixed folder is KEPT (its nested Sub/u1 leaf is unprepped)…
  const mixed = findFolder(unprepped, "Mixed");
  ok(!!mixed, "UNPREPPED prune KEEPS the Mixed folder (has a nested unprepped leaf)");
  // …and the all-archived sibling is DROPPED entirely.
  ok(
    findFolder(unprepped, "AllArchived") === null,
    "UNPREPPED prune DROPS the AllArchived sibling (no unprepped descendant)"
  );

  // Only the unprepped leaf survives under Mixed; the archived a1 leaf is gone.
  const keptUnpreppedIds = leafIds(unprepped).sort();
  eq(
    JSON.stringify(keptUnpreppedIds),
    JSON.stringify(["x-u1"]),
    "UNPREPPED prune keeps EXACTLY the unprepped leaf (x-u1), drops archived x-a1"
  );

  // The nested Sub folder is kept (it holds the matching leaf); the empty-after-
  // prune nothing else remains. Folder size while filtered = aggregate of VISIBLE
  // leaves: Mixed shows 100 (only u1), Sub shows 100.
  if (mixed) {
    eq(mixed.size_bytes, 100, "Mixed folder size = aggregate of visible (unprepped) leaves = 100");
    const sub = findFolder([mixed], "Sub");
    ok(!!sub, "the nested Sub folder is kept (it contains the matching leaf)");
    if (sub) eq(sub.size_bytes, 100, "Sub folder size = its single visible leaf = 100");
  }

  // --- prune to ARCHIVED ----------------------------------------------------
  const archived = pruneTreeByState(tree, "ARCHIVED", PRUNE_MODEL_BY_ID);
  const archIds = leafIds(archived).sort();
  eq(
    JSON.stringify(archIds),
    JSON.stringify(["x-a1", "x-a2", "x-a3"]),
    "ARCHIVED prune keeps EXACTLY the three archived leaves (incl. model-overridden x-a1)"
  );
  // Both folders survive under ARCHIVED (each has an archived descendant); the
  // Mixed folder now shows ONLY a1 (9000) — the Sub folder (only-unprepped) drops.
  const mixedArch = findFolder(archived, "Mixed");
  ok(!!mixedArch, "ARCHIVED prune KEEPS Mixed (a1 is archived)");
  ok(
    findFolder(archived, "AllArchived") !== null,
    "ARCHIVED prune KEEPS AllArchived (all its leaves are archived)"
  );
  if (mixedArch) {
    ok(
      findFolder([mixedArch], "Sub") === null,
      "ARCHIVED prune DROPS the Sub folder under Mixed (its only leaf is unprepped)"
    );
    eq(mixedArch.size_bytes, 9000, "Mixed folder size under ARCHIVED = a1 only = 9000");
  }
  const allArch = findFolder(archived, "AllArchived");
  if (allArch) {
    eq(allArch.size_bytes, 15000, "AllArchived folder size = a2 + a3 = 15000");
  }

  // --- EACH leaf appears under EXACTLY its own state filter ------------------
  // Build state -> kept leaf ids for every real state; assert the partition.
  const expectByState = {
    UNPREPPED: ["x-u1"],
    ARCHIVED: ["x-a1", "x-a2", "x-a3"],
    LOCAL_NOT_PUSHED: [],
    PUSHED_NOT_ARCHIVED: [],
    RESTORED_REPLACE_AGAIN: [],
  };
  Object.keys(expectByState).forEach(function (st) {
    const got = leafIds(pruneTreeByState(tree, st, PRUNE_MODEL_BY_ID)).sort();
    eq(
      JSON.stringify(got),
      JSON.stringify(expectByState[st].slice().sort()),
      "state filter " + st + " yields exactly its own leaves"
    );
  });

  // --- "All" keeps EVERYTHING (caller skips the prune for ALL); the prune must
  //     ALSO never mutate the input tree, so the cached /api/tree stays pristine.
  const allIds = leafIds(tree).sort();
  eq(
    JSON.stringify(allIds),
    JSON.stringify(["x-a1", "x-a2", "x-a3", "x-u1"]),
    "All = the whole tree keeps every leaf (4 total)"
  );

  // Mutation guard: re-run a prune and confirm the source tree is byte-identical.
  const before = JSON.stringify(tree);
  pruneTreeByState(tree, "UNPREPPED", PRUNE_MODEL_BY_ID);
  eq(JSON.stringify(tree), before, "pruneTreeByState does NOT mutate the input tree");
}

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

  // Scenario 2: the grouped-view state prune (folder kept iff a descendant leaf
  // matches; folder size = aggregate of visible leaves; All keeps everything).
  await runPruneScenario(fakeTree());

  if (failures > 0) {
    console.error("\n" + failures + " assertion(s) FAILED");
    process.exit(1);
  }
  console.log("\nAll data-bucket + prune assertions passed.");
}

main().catch((err) => {
  console.error("test crashed: " + (err && err.stack ? err.stack : err));
  process.exit(1);
});
