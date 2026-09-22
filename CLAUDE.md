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
  `MAX_HANDS=10`, re-hired daily and sized to owned tile count) that also runs a livestock
  operation (`ANIMAL_PLANS`: 6 cows + 3 sheep sharing PASTURE; goose support still exists in code
  but is dialed to 0 target, see below) fed from its own wheat surplus, plus a small one-shot
  melon batch (`MELON_TARGET`) for market diversification, and clears weeds via DIG to reclaim
  land. Every unit is greedily matched to the nearest task: feed unfed animals > harvest (crops +
  animal product) > water thirsty plants > place a carried animal into its empty structure > build
  new structures > plant wheat, then melon > clear weeds. Wheat (and melon) harvests are timed to
  the yield peak rather than the first eligible day, since HARVEST costs one turn either way.
  Benchmark: 40W-0L vs both `random` and `starter`, avg reward ~28,650 / ~27,350 over 40 trials —
  up from ~26,150 / ~26,350 after adding melon (see "Copying the #1 team's strategy" below),
  ~21,528 / ~21,789 after the market/dispatch bug fixes, ~17,450 / ~18,134 after fixing the day-4
  watering bug, ~10,127 / ~10,316 after capping land expansion to NE only, and ~5770 / ~5915 at
  the start of the first round of fixes.
- Confirmed against the installed `kaggle_environments` source (not just the README, which was
  ambiguous here): FEED and PLACE consume from the *acting unit's own inventory*, not the shared
  shed, so animals/wheat must be PICKUP'd from the shed before use. BUILD_COOP/BUILD_PASTURE cost
  zero gold, only one action turn.
- **Seven real bugs found so far, not just tuning** (most via replay analysis — dump one with
  `run_match.py <opp> --replay out.json` and inspect money/tile-state over time — a couple by
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
  5. The shed-surplus SELL loop queued `SELL` orders for COW/GOOSE/SHEEP sitting in the shed
     awaiting PICKUP+PLACE. The engine's SELL only accepts real products, so these silently no-op
     — but `maxMarketOrdersPerTurn` truncation happens on the raw list *before* validation, so a
     dead order could still crowd out a real one later in the list. Fix: exclude animal names from
     the shed-surplus sell loop.
  6. Two "one-size-fits-all" dispatch bugs: the feed tier's wheat-pickup fallback and the place
     tier's animal-pickup fallback both computed a needed quantity once and handed it unchanged to
     *every* actor `assign()` matched that turn, instead of splitting/capping it. Two-plus actors
     already standing on distinct shed-access tiles would each get the full un-decremented
     request — over-fetching wheat, or (for animals, where PICKUP silently caps at available
     stock) wasting a whole actor-turn on a request that resolves to 0. Fix: a shared, decrementing
     budget gated through assign()'s `feasible` check, only actually decremented once a dispatch
     is confirmed (an initial version decremented speculatively before confirming a match, which
     could under-count real shed stock for a later tile the same turn — fixed on the second pass).
  7. Hire-hand budget was computed independently from the same `money - CASH_RESERVE` pool that
     seed/animal/land purchases also draw from, using a separate non-decremented copy. Since HIRE
     is deliberately queued last in the market list and the engine executes a turn's orders
     strictly in list order (deducting real money as each commits), a big same-turn purchase could
     already spend money HIRE's sizing loop still believed was available, silently under-hiring
     with no error. Fix: one running `available` threaded through seed → animal → land → hire in
     their actual execution order.
  A first attempt at adding cows (before bugs 1-3 were fixed) looked like a clean regression in
  isolated A/B testing and was reverted; retrying the *identical* idea after fixing bugs 1-3
  turned it into the single biggest win of that session. Lesson: when a plausible feature
  regresses, suspect an interacting bug before concluding the feature itself is bad — a replay
  dump (or, per bug 4, just re-reading the engine source next to the agent's own logic) can answer
  it in minutes.
- **Studying real leaderboard opponents beats guessing at the next feature.** After submitting,
  pull the submission's played episodes and inspect them like any other replay: `kaggle
  competitions episodes <submission_id>` lists episode IDs, `kaggle competitions replay
  <episode_id> -p <dir>` downloads each one as the same JSON shape `run_match.py --replay`
  produces (`info.TeamNames` tells you which `farms[i]` is you vs. the opponent — public tiles are
  visible for both sides even though `private` inventory/shed is not). Comparing our farm's tile
  composition over time against a top-scoring opponent's (one scored 118k vs. our 21k in the same
  episode) is what surfaced the cow+sheep livestock strategy below and the wind-down-crops-near-
  season-end idea (not yet implemented) directly from data, instead of speculating from the rules.
- **Livestock scale-up (cow+sheep, dropping goose) took several replay-guided rounds to get
  right — herd size is not a dial that just goes up.** The opponent replay above showed 9 cows +
  4 sheep and zero geese (goose's ~$50/day is the weakest of the three vs. cow's ~$80 and sheep's
  ~$67). Naively raising `ANIMAL_PLANS` targets straight to that scale (8+5+6=19 structures)
  caused a death spiral: reserving that much land/actor-turns from turn 0, before any wheat income
  existed, let weeds compound (14 by day 20 in one replay), collapsed wheat capacity (46→4 tiles),
  and starved the whole herd out (14→1 animals) — confirmed via replay, not guessed. Backing off to
  smaller targets *still* underperformed the original pilot at first, for two separate reasons
  also only visible via replay: cow and sheep share `PASTURE`, and an empty pasture was being
  earmarked for whichever plan was furthest from its own *overall target* — cow's target rarely
  finished, so sheep never got a turn even sitting bought-and-ready in the shed (all 5 target sheep
  sat dead the whole game in one run); separately, an incremental structure-build throttle (added
  to fix the turn-0 reservation problem) delayed even small targets from ever fully building out.
  Both were code bugs, fixed by deciding the animal-per-tile at PLACE time from what's actually on
  hand (not from target distance) and by dropping the incremental-build throttle now that targets
  are sized to what current actor count can sustain. With those fixed, 6 cow + 3 sheep (no goose)
  beat the original pilot by ~20%. Also found: reward is *not monotonic* in herd size near this
  actor count's ceiling — 8 cow + 3 sheep (11 total) collapsed *worse* than 9 cow + 4 sheep (13
  total) did in local testing, so this is a genuine cliff/chaos region, not a smooth curve; treat
  any number in that range as unreliable rather than trying to rank them further. See `ANIMAL_PLANS`
  in `main.py` for the blow-by-blow.
- **Copying the #1 team's strategy: do it one small, gated step at a time, not all at once.**
  Pulled the #1 team's own replay directly (works for ANY episode ID, not just ones we played in —
  `kaggle competitions replay <episode_id>`, found via the leaderboard's episode links) and found a
  much bigger strategy than our wheat+cow+sheep setup: 3 land quadrants (75 tiles, we only buy NE),
  wheat + melon + strawberry + tomato simultaneously, ~18-23 animals of all three types, cash run
  down to $1-70 repeatedly, and crops wound down near day 29. A first attempt implemented ALL of
  this in one pass and it collapsed hard (one game finished at 394 total money, essentially a
  wipeout) despite six separate rounds of real, replay-confirmed bug fixes along the way (cash
  reservation order, actor-capacity caps on new planting, wheat-vs-crop land priority, watering
  urgency) — every fix was individually correct and none of them were enough, because the
  underlying problem was architectural: this agent's greedy nearest-neighbor task assignment has
  no global capacity planning, and three new crop types plus more land multiplied the ways it could
  spread itself too thin before any of the new investments paid off. Restarted with ONE piece at a
  time instead:
  - **Land alone (buying SW, nothing else changed) was tried first and is a regression** — weeds
    climbed from 3 to 26 tiles by day 29. `MAX_HANDS=10`/`TILES_PER_ACTOR=5` was tuned for ~5
    tiles/actor on 50 tiles; holding that ratio on 75 tiles needs hands expensive enough
    (Fibonacci: the 14th hand costs $377/day) to not be worth it. The #1 team affords more land
    with *fewer* hands (8 for 75 tiles) specifically because their ongoing crops (strawberry,
    tomato) need less attention per tile than a continuously-replanted wheat monoculture — land
    expansion only pays off *together with* those crops, not before them. Reverted; land stays at
    NE-only until crop diversification is further along.
  - **Melon alone, small and gated, is a validated win**: `MELON_TARGET=6`, planted only in a
    narrow window (days 5-7, gated behind the same `wheat_flowing` day wheat's own bug-fix history
    already established) so wheat fully establishes first, exactly like the original wheat-only
    design did. 40W-0L, ~28,650/~27,350 avg reward, up from ~26,150/~26,350 — melon's $250 base
    price (vs. wheat's $25) pays off heavily even at a target of just 6 tiles, planted once and
    never replanted (matching the #1 team's own one-shot pattern), without needing any of the
    actor-capacity machinery the failed big-bang attempt built (that was reverted along with
    everything else — small, gated targets didn't need it).
  - Next candidates, each to be added and replay-validated alone before the next: strawberry
    (ongoing crop — first real test of the `_daily_refresh_plants` scheduled-production mechanics,
    since melon is one-time like wheat), then tomato, then revisit SW land once crop diversity
    is far enough along to justify it, then a bigger animal herd, then cash-reserve tightening,
    then endgame wind-down. Test order follows how self-contained and low-risk each piece is, not
    the #1 team's own likely order.
- Not yet using: SW/SE land (see above), strawberry/tomato/carrot, fertilizer for crops, a bigger
  animal herd matching the #1 team's ~18-23, tighter cash management, or an endgame crop/hand
  wind-down (the studied opponent had 0 hands and 0 planted crops by day 29, presumably because a
  freshly-planted crop can't mature before season end that late). Further layers should be
  validated the same way this round was — replay-inspected, not just win/loss, one variable at a
  time — since the real leaderboard (thousands of tuned competitor bots, currently ranking us
  ~7000th of ~9700 at a 491 score vs. leaders around 3000) is a much higher bar than these two
  fixed baselines.
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
