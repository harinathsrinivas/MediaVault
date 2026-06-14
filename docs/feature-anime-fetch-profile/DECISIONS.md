# IMP-C16 — Open Decision Log

Recorded decisions made during planning and execution of fix/anime_fetch_profile.

| # | Question | Options | Choice | Rationale |
|---|----------|---------|--------|-----------|
| OD-1 | How data-driven should the prefix→profile map be, given IMP-A5 is still pending? | A: in-module constant now / B: full mvconfig.json wiring now (pull A5 forward) / C: literal elif hardcode | **A** | C16 is a small low-risk fix; IMP-A5 is a separate Band-2 task. In-module constant satisfies the "data-driven" goal structurally — two constants to edit for a new account — without A5's blast radius. IMP-A5 will source these from mvconfig.json later. |
| OD-2 | Rename the `"default"` movies profile key to `"movies"`? | A: rename to "movies" (content-named) / B: keep "default" | **A** | Clean, consistent content-named keys across all three (movies/tv/anime). Internal dict key only — on-disk ChromeProfile path unchanged, no re-login needed. |
| OD-3 | Profile path and one-time manual login | Path: C:\Media\Utils\ChromeProfile_Anime / User does manual login | **Confirmed** | User created the folder, opened Chrome with that user-data-dir, and signed into the anime Google account at photos.google.com. |
| OD-4 | Test scope: routing-only stub or live end-to-end Chrome fetch? | A: live end-to-end (real Chrome, real Google Photos) / B: routing-only stub | **A** | User explicitly requested live verification. Ran all 3 profiles (anime/tv/movies): each opened Chrome on the correct user-data-dir and Selenium attached cleanly. Downloads skipped (fetch_single_entry patched to no-op) since all anime files are 2.3+ GB. Routing is the fix; download correctness is unchanged behavior. |
