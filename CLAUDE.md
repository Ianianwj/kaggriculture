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
- Land expansion (`BUY_LAND`, $1k/$2k/$4k) and hiring farm hands (`HIRE`, Fibonacci-priced per
  day) both cost cash up front for more parallel actions later — timing matters more than the
  raw ROI number.
- Current agent (`main.py`) is a multi-tile, multi-actor wheat farm (farmer + hired hands, up to
  `MAX_HANDS=10`, re-hired daily and sized to owned tile count) that also runs a small livestock
  operation (`ANIMAL_PLANS`: cows then geese, prioritized by $/wheat-fed) fed from its own wheat
  surplus, and clears weeds via DIG to reclaim land. Every unit is greedily matched to the nearest
  task: feed unfed animals > harvest (crops + animal product) > water thirsty wheat > place a
  carried animal into its empty structure > build new structures > plant wheat > clear weeds.
  Wheat harvests are timed to the yield peak (day 4) rather than the first eligible day (day 2)
  since HARVEST costs one turn either way. Benchmark: 20W-0L vs both `random` and `starter` (avg
  reward ~10,127 / ~10,316 over 20 trials) — up from ~5770 / ~5915 at the start of this round of
  fixes (see below), ~75% higher.
- Confirmed against the installed `kaggle_environments` source (not just the README, which was
  ambiguous here): FEED and PLACE consume from the *acting unit's own inventory*, not the shared
  shed, so animals/wheat must be PICKUP'd from the shed before use. BUILD_COOP/BUILD_PASTURE cost
  zero gold, only one action turn.
- **Three real bugs found via replay analysis, not just tuning** (dump one with
  `run_match.py <opp> --replay out.json` and inspect money/tile-state over time — local win/loss
  against weak baselines does NOT surface these, since the baselines are weak enough to lose
  even to a buggy agent):
  1. Priority order had HARVEST before FEED, so any actor holding wheat from a harvest got
     redrafted into the next harvest before ever delivering it to a hungry animal — animals
     starved and escaped in cycles, repeatedly burning their full purchase cost. Fix: feed first.
  2. Animals bought on day 0 have zero feed source (wheat harvests are deliberately delayed to
     day 4+ for yield-peak timing), guaranteeing starvation. Fix: gate all `BUY_ANIMAL` on
     `obs["day"] > WHEAT_MAX_YIELD_DAY`.
  3. `MAX_HANDS` was capped at 4 (right for the 25-tile starting quadrant) but land expansion
     grows to 100 tiles — actor count never scaled up, leaving 40-60 tiles idle late-game. Fix:
     raise the cap based on hire-cost economics (Fibonacci cost vs. ~$90-100/day value per hand).
  A first attempt at adding cows (before these fixes existed) looked like a clean regression in
  isolated A/B testing and was reverted; retrying the *identical* idea after fixing bugs 1-3
  turned it into the single biggest win of the session. Lesson: when a plausible feature
  regresses, suspect an interacting bug before concluding the feature itself is bad — a replay
  dump answered it in minutes.
- Not yet using: land expansion beyond the opportunistic `BUY_LAND` check, fertilizer for crops,
  sheep, or other crops (carrot/tomato/melon). Since we're winning cleanly against both local
  baselines, further layers should be validated the same way this round was — replay-inspected,
  not just win/loss — since the real leaderboard (thousands of tuned competitor bots) is a much
  higher bar than these two fixed baselines.

## Testing before submitting

Always run `python run_match.py random --trials 20` and `python run_match.py starter --trials 20`
after a strategy change. Only submit via `kaggle competitions submit` when both show a clear
positive win rate — submissions are rate-limited, so don't burn one to test something local
testing can already answer.
