# Kaggriculture

Two-player farming-sim competition on Kaggle, run via the `kaggle-environments` package.
Full rules: [README.md](README.md) (mechanics, market price function, config defaults).
Submission/CLI workflow: [AGENTS.md](AGENTS.md) (agent contract, local testing, `kaggle` CLI).

## Setup

- Dependencies (`kaggle-environments`, `kaggle`) are installed into the global Python, not a
  venv. A venv nested inside this OneDrive-synced folder hit Windows' 260-char path limit on one
  of kaggle-environments' dependencies (orbax-checkpoint ships deeply nested test fixtures), and
  OneDrive syncing tens of thousands of venv files is its own headache — global install sidesteps
  both.
- This machine just had Python/Git installed via winget, and Windows Long Path support was
  enabled (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1`) to fix the
  above. If a shell can't find `python`/`git`, restart the terminal so PATH picks up the install.

## Project layout

- `main.py` — the submission entrypoint (`agent(obs)` function). This is what gets submitted.
- `run_match.py` — local test harness. Run `python run_match.py random --trials 20` to play
  many games against a baseline and see win rate / average reward before spending a submission.
- Baseline opponents available by name: `"pass"`, `"random"`, `"starter"`.

## Strategy notes

- The season is 720 turns (24/day x 30 days) and outcomes are noisy (weed spawns, random town
  shop unlocks), so judge strategy changes over `--trials 20+`, not a single match.
- Key economic levers: crop/animal choice trades off seed cost, time-to-yield, and market
  reaction (premium goods like melon/strawberry/milk/wool crash hard on gluts — stagger sells).
- Land expansion (`BUY_LAND`, $1k/$2k/$4k for NE/SW/SE) and hiring farm hands (`HIRE`,
  Fibonacci-priced per day) both cost cash up front for more parallel actions later — timing
  matters more than the raw ROI number. But land is only worth buying if it can actually be
  staffed: at `MAX_HANDS=10` (11 actors x `TILES_PER_ACTOR=5` ≈ 55 tiles), NW+NE (50 tiles) is
  already close to the ceiling, so `main.py` only buys NE and skips SW/SE — the $6000 combined
  cost was sitting in unstaffable land instead of hands/animals/seed, capital that compounds over
  720 turns. Replay confirmed tile utilization doesn't improve past ~2 quadrants at this actor cap.
- Current agent (`main.py`) is a multi-tile, multi-actor wheat farm (farmer + hired hands, up to
  `MAX_HANDS=10`, re-hired daily and sized to owned tile count) that also runs a small livestock
  operation (`ANIMAL_PLANS`: cows then geese, prioritized by $/wheat-fed) fed from its own wheat
  surplus, and clears weeds via DIG to reclaim land. Every unit is greedily matched to the nearest
  task: feed unfed animals > harvest (crops + animal product) > water thirsty wheat > place a
  carried animal into its empty structure > build new structures > plant wheat > clear weeds.
  Wheat harvests are timed to the yield peak (day 4) rather than the first eligible day (day 2)
  since HARVEST costs one turn either way. Benchmark: 20W-0L vs both `random` and `starter` (avg
  reward ~21,421 / ~21,900 over 20 trials) — up from ~17,450 / ~18,134 after fixing the day-4
  watering bug (see below), ~10,127 / ~10,316 after capping land expansion to NE only, and
  ~5770 / ~5915 at the start of this round of fixes.
- Confirmed against the installed `kaggle_environments` source (not just the README, which was
  ambiguous here): FEED and PLACE consume from the *acting unit's own inventory*, not the shared
  shed, so animals/wheat must be PICKUP'd from the shed before use. BUILD_COOP/BUILD_PASTURE cost
  zero gold, only one action turn.
- **Four real bugs found so far, not just tuning** (three via replay analysis — dump one with
  `run_match.py <opp> --replay out.json` and inspect money/tile-state over time — and one by
  diffing the agent's own logic against the installed engine source; local win/loss against weak
  baselines does NOT surface these, since the baselines are weak enough to lose even to a buggy
  agent):
  1. Priority order had HARVEST before FEED, so any actor holding wheat from a harvest got
     redrafted into the next harvest before ever delivering it to a hungry animal — animals
     starved and escaped in cycles, repeatedly burning their full purchase cost. Fix: feed first.
  2. Animals bought on day 0 have zero feed source (wheat harvests are deliberately delayed to
     day 4+ for yield-peak timing), guaranteeing starvation. Fix: gate all `BUY_ANIMAL` on
     `obs["day"] > WHEAT_MAX_YIELD_DAY`.
  3. `MAX_HANDS` was capped at 4 (right for the 25-tile starting quadrant) but land expansion
     grows to 100 tiles — actor count never scaled up, leaving 40-60 tiles idle late-game. Fix:
     raise the cap based on hire-cost economics (Fibonacci cost vs. ~$90-100/day value per hand).
  4. The per-tile tier classification sent a wheat plant straight to `harvest_targets` the instant
     `age >= WHEAT_MAX_YIELD_DAY`, so it was never watered on day 4 itself — even though the
     engine's own watering-bonus window (`window_start <= age <= max_yield_day`) is inclusive of
     that day. Cost 1 of 4 possible yield units on *every single wheat cycle*, tile, all game —
     the single highest-leverage bug found, worth ~20% avg reward on its own. Fix: only route to
     harvest once watered today (or once the window has fully passed).
  A first attempt at adding cows (before bugs 1-3 were fixed) looked like a clean regression in
  isolated A/B testing and was reverted; retrying the *identical* idea after fixing bugs 1-3
  turned it into the single biggest win of that session. Lesson: when a plausible feature
  regresses, suspect an interacting bug before concluding the feature itself is bad — a replay
  dump (or, per bug 4, just re-reading the engine source next to the agent's own logic) can answer
  it in minutes.
- Not yet using: SW/SE land (deliberately, see above — revisit if `MAX_HANDS` or
  `TILES_PER_ACTOR` change), fertilizer for crops, sheep, or other crops (carrot/tomato/melon).
  Since we're winning cleanly against both local baselines, further layers should be validated the
  same way this round was — replay-inspected, not just win/loss — since the real leaderboard
  (thousands of tuned competitor bots) is a much higher bar than these two fixed baselines.
- **Tried and reverted: spending fertilizer on wheat.** Engine confirms `FERTILIZE` raises wheat's
  max yield 4→6 (worth doing), and it's free — collected off animals via `COLLECT_FERTILIZER`
  (already implemented as an idle-time bonus action, tier 8). But adding a tier to actually spend
  it on wheat moved the benchmark by less than trial-to-trial noise (~21,600/~21,600 either way
  over 40 trials, vs. ~21,421/~21,900 without it). Replayed a full 720-turn game to find out why
  instead of guessing: only 3 `COLLECT_FERTILIZER` calls and **zero** `FERTILIZE` calls fired in
  the whole game. Root cause: collection is gated on an actor being *fully idle* standing on the
  animal's own tile, which with ~9 hands covering ~50 tiles almost never happens — there's always
  a higher-priority task. The fertilizer-spending logic itself was never wrong, it just never had
  any fertilizer to spend. Reverted rather than keep dead code. To make this lever real, collection
  would need its own routed tier (like weeds/DIG) instead of riding on leftover idle time, and that
  routing cost (actor-turns diverted from tending ~50 wheat tiles) needs its own A/B test before
  assuming it's a net win.

## Testing before submitting

Always run `python run_match.py random --trials 20` and `python run_match.py starter --trials 20`
after a strategy change. Only submit via `kaggle competitions submit` when both show a clear
positive win rate — submissions are rate-limited, so don't burn one to test something local
testing can already answer.
