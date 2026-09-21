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
- Current agent (`main.py`) is a single-farmer, multi-tile wheat farm: it works every owned
  tile (not just one), prioritizing harvest > water > expand, and times harvests to wheat's
  yield peak (day 4) instead of the first eligible day (day 2) since HARVEST costs one turn
  either way. Benchmark: 20W-0L vs `random` (avg reward ~5010), 16W-4L vs `starter` (avg
  reward ~4828), each over 20 trials.
- Not yet using: hired hands (more parallel actions/turn), land expansion beyond the opportunistic
  BUY_LAND check, fertilizer, other crops, or animals. Hands are probably the next highest-leverage
  layer now that the starting quadrant is fully utilized — a second/third actor multiplies
  everything the tile-scheduling logic already does.

## Testing before submitting

Always run `python run_match.py random --trials 20` and `python run_match.py starter --trials 20`
after a strategy change. Only submit via `kaggle competitions submit` when both show a clear
positive win rate — submissions are rate-limited, so don't burn one to test something local
testing can already answer.
