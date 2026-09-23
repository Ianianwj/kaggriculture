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
  melon batch (`MELON_TARGET`) planted in parallel with wheat from day 0 for market
  diversification and a cheap CARROT batch on whatever land wheat's own window has closed on near
  season end, spends fertilizer collected from its own animals to boost wheat's yield cap, and
  clears weeds via DIG to reclaim land. Every unit is greedily matched to the nearest task: feed
  unfed animals > harvest (crops + animal product) > water thirsty plants > fertilize a wheat tile
  in its watering-bonus window (using fertilizer already carried) > place a carried animal into
  its empty structure > build new structures > plant wheat, then melon, then carrot > collect
  fertilizer from fed animals for a future turn > clear weeds. Wheat (and melon) harvests are
  timed to the yield peak rather than the first eligible day, since HARVEST costs one turn either
  way. Hiring is sized and its cost reserved from the shared cash pool FIRST, before seed/animal/
  land purchases can spend into it (the `HIRE` orders themselves still queue last in the market
  list, a separate concern -- see "Copying the #1 team's strategy" below). Benchmark: 40W-0L vs
  both `random` and `starter`, avg reward ~42,800 / ~42,800 over 40 trials — up from ~34,100 /
  ~34,000 after raising `MELON_TARGET` 6 -> 12 (a sweep that only became possible once melon
  planted from day 0; see below), ~34,100 /
  ~33,100 after adding carrot as a cheap endgame crop on land wheat's own window has closed on
  (see below), ~31,700 / ~32,000 after un-gating melon to plant in parallel with wheat from
  day 0 instead of waiting until day 5 (see "Copying the #1 team's strategy" below for why: the
  #1 team's own replay shows them doing exactly this, and staged sequential buildup rather than
  any single parameter turned out to be the real bottleneck behind six failed land-scaling
  attempts), ~31,700 / ~31,900 after promoting the fertilizer-collection tier's priority (that
  round's hire-priority reorder was a small, standalone win; SW land + strawberry paired with it
  was tried and reverted, see below), ~28,650 / ~27,350 after adding a routed fertilizer tier (see
  "Copying the #1 team's strategy" below), ~26,150 / ~26,350 after adding melon,
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
  - **Tried and reverted: strawberry, first attempt.** `STRAWBERRY_TARGET=8`, window days 5-10
    (same start day as melon's). Two bugs before even getting to the economics: (1) the two crops'
    windows overlapping on day 5 piled up seed-buying/planting for both plus that day's first
    animal purchases onto the same handful of actors — weeds spiked to 27 tiles by day 10 in most
    trials, 2W-18L vs `starter`. Fixed by staggering strawberry's window to start day 10 (3 days
    after melon's closes, via a new `first_plant_day` field generalizing what was a shared
    `WHEAT_MAX_YIELD_DAY` gate). (2) With that fixed, replay showed seeds bought early in a window
    can sit **permanently unplanted** if actors are busy the turns that follow: land reservation
    and seed-buying were both gated by the same `target vs. existing` check that reads 0 once the
    window closes, so `existing` staying 0 (nothing got planted before the window ended) reopens
    nothing — 6 bought strawberry seeds sat in inventory the entire rest of one game. Fixed by
    widening the window (7 days instead of 3) to give planting more chances to actually happen. Both
    fixes were real and are in `_classify_plant`/`CROP_PLANS`' `first_plant_day`, but the *economics*
    still didn't pencil out even after both: 40W-0L but avg reward ~18,200/~19,050, well below
    melon-only's ~28,650/~27,350. Root cause, worked out after the bugs: melon's win was driven by
    a 10x price premium over wheat (comfortably worth dedicating land to for even one un-replanted
    cycle); strawberry's premium is only ~4.8x (`$120` vs. wheat's `$25`) while its full cycle
    (plant to end of scheduled production) is ~19 days vs. wheat's ~5 — planted once and never
    replanted, those tiles earn less over the season than continuously-replanted wheat would have
    on the same land. The mechanics work correctly (harvests immediately on each scheduled
    production, decays naturally once `max_yield` productions complete and it's left unharvested-in-
    place); this genuinely isn't worth doing as a single un-replanted batch the way melon is.
    Reverted rather than keep a net-negative crop.
  - **Tried and reverted: a larger melon target.** Raised `MELON_TARGET` 6→10, nothing else
    changed. Also a regression: 20W-0L vs `random` but only 15W-5L vs `starter`, avg reward
    ~13,300/~12,900. Even an already-*proven* crop isn't free to scale — the extra 4 tiles'
    seed cost (+$320) and land claim land in the exact same narrow day 5-7 window, competing with
    that same day's first animal purchases for the same handful of actors. Reverted to 6.
  - **Tried and reverted: tomato.** `TOMATO_TARGET=6`, staggered to start day 10 (well after
    melon's day 5-7 window closes) specifically to avoid repeating strawberry's window-collision
    bug. That collision didn't recur, but it's a regression anyway: 20W-0L both baselines (no
    losses, unlike strawberry's crash) but avg reward ~15,900/~14,900, still well below
    melon-only's ~28,650/~27,350. One replay showed a weed spike to 19 tiles by day 10 — right as
    tomato's window opens but before any tomato could physically be planted yet, so this doesn't
    look like tomato's own contention the way strawberry's day-5 overlap was; more likely this
    farm's actor economy is simply *fragile* near this size (melon-scale-up regressed via a
    similar mechanism without any staggering issue at all). Reverted; needs more replay diagnosis
    before retrying, not just a target/timing tweak.
  - **Emerging pattern across all three reverted attempts**: this agent's economy, as currently
    architected, sits at a narrow, fairly fragile local optimum around wheat + melon(6) +
    cow(6)/sheep(3) — the "add one small thing" playbook that worked once for melon doesn't
    reliably generalize to the next crop, even staggered and even when the new crop's own
    mechanics work correctly. Whatever headroom remains likely needs either genuinely idle actor
    capacity to spend (more hands, which the Fibonacci cost curve makes expensive) or a crop
    with melon-like economics (large price multiple relative to wheat, short enough cycle to
    still pay off unreplanted) rather than mid-tier ones like strawberry/tomato. Candidates not
    yet tried: strawberry/tomato *with replanting* (more code, might unlock value a single batch
    can't), SW land only after that headroom exists, a bigger animal herd, tighter cash, endgame
    wind-down — each still needs its own isolated replay validation, not a bundle.
  - **Investigated whether the #1 team's edge is market-price adaptivity — mostly not, but it
    surfaced a real structural gap: fertilizer.** Compared action *types*, not just tile
    composition, against the same #1-team replay. Their SELL behavior turned out to be "dump
    whatever's in the shed most turns" — the same policy our own SELL loop already runs — not
    price-timed holding: directly checked by comparing each turn's shed stock of every product
    against that turn's SELL quantity, and while they do hold back stock on 76-100% of turns
    depending on product, that lines up with `maxMarketOrdersPerTurn` (10) forcing them to
    prioritize across up to 9 simultaneously-sellable products, not with watching price trends.
    One melon dump (36 units over 2 days, visibly crashing its own price 271→136) confirms
    they don't hold back one-shot harvests either — same "just sell it" policy we already have.
    The one real gap market data surfaced: they run `FERTILIZE`/`COLLECT_FERTILIZER`
    constantly (490 collects / 237 fertilizes over 720 turns) where we'd tried this exact lever
    before and reverted it as dead code (see below) after it never fired. Re-diagnosed why
    theirs fires and ours didn't: the mechanic itself was never the problem, only where it sat
    in priority — ours was tier-8 idle-fallback-only, and idle time basically doesn't exist with
    ~9 hands covering ~50 tiles. Fixed by giving both actions real `assign()`-routed tiers
    (actors get moved toward them, not just used when one happens to already be standing there):
    tier 3b spends any fertilizer an actor is already carrying on a wheat tile still inside its
    watering-bonus window, and tier 7b (low priority, after weeding) sends idle actors to collect
    from fed animals for a future turn's tier 3b to spend. Confirmed against engine source: one
    `FERTILIZE` call at wheat age 2 covers all 3 days of its watering-bonus window with a single
    unit, raising wheat's realistic cap from 4 (watering alone) to its true max of 6 — a 50%
    yield boost from a byproduct animals already make for free. Melon is deliberately excluded:
    engine math confirms it already saturates its own cap (6) through watering alone by age 10
    (see README), so spending fertilizer there is wasted. Validated over 40 trials:
    28,891.9/27,853.9 avg reward (up from melon-only's 28,654.2/27,347.2), 40W-0L both
    baselines — small but real, no regressions. Replay-checked and only 12 `FERTILIZE` calls
    fired in that game, well under the #1 team's per-animal rate, since tier 7b still sits
    behind weeding and is starved for actor-turns — raising its priority is an open follow-up,
    not yet tried.
  - **Filled that gap: promoting the collection tier's priority, in two tries.** First tried
    moving `COLLECT_FERTILIZER` from tier 7b (after weeding) to right after the fertilize-spend
    tier (ahead of place/build/plant/weed) -- volume jumped a lot (171 collects / 84 fertilizes
    in one replay, vs. 26/12 before), but it was a clear regression: vs `starter` dropped to
    19W-1L (one of very few losses this agent has ever taken against either baseline) at
    23,897.2 avg reward, because that many actor-turns diverted from watering/harvesting/
    planting cost more than the extra fertilizer was worth. Backed off to a smaller promotion
    instead -- landing it as tier 6c, after both plantings but still ahead of weeding, so it
    only competes with weeding (low-urgency) rather than anything with a hard deadline (feed,
    water) or that claims land/cash (place, build, plant). That's the sweet spot: 57 collects /
    21 fertilizes in one replay (meaningfully more than the original 26/12, nowhere near the
    186/84 that broke things), `WATER`/`HARVEST`/`PLANT` counts staying close to their original
    levels. Validated over 40 trials: 31,696.8/31,895.0 avg reward, 40W-0L both baselines --
    up ~10-15% from the already-validated 28,891.9/27,853.9. Lesson: for a tier whose value
    doesn't expire if delayed (fertilizer collection has no deadline), the right priority isn't
    "as high as possible" or "as low as possible" but wherever it stops competing with
    deadline-bound tasks -- worth remembering before promoting any other low-urgency tier the
    same way.
  - **Tried and reverted: copying the #1 team's cash aggression.** They run money down to
    $1-70 nearly every single day (see the DSM money trajectory earlier in this section); our
    `CASH_RESERVE=50` always leaves a fixed buffer untouched. Dropped it to 5 alone, nothing
    else changed -- a clear regression: ~27,100/~28,200 avg reward (down from 31,696.8/31,895.0),
    still 0 losses but a real drop. Replay-diagnosed rather than just reverting blind: hand count
    went erratic (crashed to 0-1 hands on several days instead of a steady ~7-9), and **zero**
    `PLANT MELON` actions fired the entire game -- the 6 melon seeds bought around day 6 sat
    stranded, unplanted, for the rest of the season (same failure shape as the strawberry
    seed-stranding bug, different cause). Root cause: `HIRE` is deliberately sized last, against
    whatever `available` cash survives that turn's seed/animal/land purchases (see "Shared cash
    pool" comment in `main.py`) -- with only a $5 floor, those purchases regularly spend it all
    before hire-sizing ever runs, crashing hand count on exactly the days melon's narrow
    day-5-7 window needs actor-turns most. The #1 team can run this lean because their hiring
    isn't competing against a hard-gated one-shot planting window the same way ours is (their
    crops ramp up gradually over many days -- see the strawberry ramp-up finding above -- rather
    than needing a burst of actor-turns in one narrow week). Reverted to 50. Copying their cash
    aggression for real would need `HIRE` to have its own reserved floor *before* seed/animal/
    land purchases claim the shared pool, not just a smaller reserve number -- a bigger, riskier
    change than a one-line constant tweak, not attempted this round.
  - **Tried and reverted: SW land paired with a dedicated crop (strawberry) instead of more
    wheat, plus raising `MAX_HANDS` to match.** The earlier land-alone regression (above) blamed
    wheat specifically -- MAX_HANDS=10/TILES_PER_ACTOR=5 tuned for a continuously-replanted wheat
    monoculture, not more of the same on 75 tiles. Retried buying SW but routing 100% of it to
    STRAWBERRY instead (an *ongoing* crop, never replanted after its one planting, matching the
    #1 team's own pattern), carving SW-quadrant tiles out by coordinate (`x < half and y >= half`)
    before wheat/melon/structures ever see them, so the existing NW/NE logic needed zero changes.
    Still a clear regression: ~25,300/~24,800 avg reward (down from 31,696.8/31,895.0), 0 losses
    but a real drop. Replay showed weeds climbing to 18-23 tiles and animals visibly dying (cow
    6->3, sheep 3->1) after SW came online -- turns out strawberry needs daily watering to avoid
    weed conversion **just like wheat** (README/engine: only the yield-accrual mechanism differs
    between one-time and ongoing crops, not the watering-to-avoid-weeds requirement), so pairing
    SW with a different crop did NOT reduce the actor-turn demand the "less actor-intensive
    ongoing crop" framing implied -- 75 tiles needs more hands than 50 regardless of what's
    planted on the extra 25. Raised `MAX_HANDS` 10->14 to match (75 // 5 - 1) as a second try,
    still bundled with SW+strawberry: barely moved the needle (~25,500/~25,450, +1-2%). Replay
    showed why: hand count *never reached the old cap of 10*, let alone 14 -- it fluctuated
    2-9 the whole game, because the $2000 SW purchase plus ongoing strawberry seed costs compete
    with wheat/melon/animal/hire spending from the same `available` pool (see "Shared cash pool"
    in `main.py`), and that pool is too thin this early (SW gets bought the moment `money >=
    $4000`, often right as the base economy is still establishing) to absorb a big land purchase
    without crashing hire-sizing on the turns that follow. So the bottleneck wasn't crop-tending
    cost (first hypothesis) or actor-count ceiling (second hypothesis) -- it's **cash-flow timing**:
    the same root cause as the cash-aggression regression above, just triggered by a lump purchase
    instead of a smaller daily reserve. Reverted both changes. This is the fourth distinct
    scale-up attempt to fail for a variant of the same underlying reason (land, cash aggression,
    land+different-crop, land+different-crop+more hands all regressed) -- strong evidence this
    agent's current architecture (single shared cash pool, buy-the-moment-affordable land/seed
    logic, no reserved budget per initiative) has a real ceiling around ~31-32k that isn't
    reachable by tuning one more variable. A genuine fix would need explicit cash budgeting (e.g.
    don't commit to a big purchase until a reserve well beyond current running costs exists, not
    just `money >= cost * 2`) rather than another isolated constant change -- a real design
    change, not attempted this round.
  - **Implemented that fix (hire-priority reordering) -- validated small win alone, still not
    enough to unlock SW+strawberry.** The identified root cause across every scale-up failure was
    that `HIRE` was sized LAST, against whatever `available` cash survived that turn's seed/
    animal/land purchases -- so a big same-turn purchase could starve hand count with zero
    protection. Fixed directly: hire-sizing now runs FIRST and reserves its cost out of
    `available` before anything else can spend it (the actual `HIRE` market orders still get
    appended to the list LAST, which is the unrelated order-count-truncation concern from bug #7
    -- reordering the CASH claim and reordering the LIST position are two different things, only
    the first was ever the bug). Tested alone (no SW/strawberry, just the reorder on the existing
    50-tile base): 31,536.5/32,185.9 avg reward over 40 trials, matching/slightly beating the
    31,696.8/31,895.0 baseline -- a genuine small win standing alone, not just neutral. Then
    retried the full SW+strawberry+`MAX_HANDS=14` combination on top of this fix: still a clear
    regression (~25,600/~25,600), barely different from every earlier attempt. Replay showed why:
    hand count was now stable *within* a turn (the bug is fixed) but still dipped to 2-3 hands on
    several days, because the underlying **daily cash total** is genuinely low those days -- SW's
    $2000 purchase plus wheat/melon/strawberry/animal running costs simply outspend income for a
    stretch, so there's nothing left to reserve regardless of claim order. Tried gating SW behind
    a much higher floor (`money >= $10,000`, roughly what the healthy 50-tile base alone reaches
    by day 20+) instead of the same `cost * 2` NE uses: still no real improvement (~26,000/~24,900).
    Reverted SW/strawberry/`MAX_HANDS` entirely (back to NE-only, `MAX_HANDS=10`) but **kept** the
    hire-priority reorder, since it's a validated win on its own. This is now six variations of
    the SW scale-up (wheat, cash aggression, strawberry, strawberry+hands, +hire-priority-fix,
    +delayed-purchase) that have all failed, each for a diagnosed, non-speculative reason -- strong
    evidence the ~31-32k ceiling is a genuine multi-day cash-flow capacity limit of funding this
    many simultaneous initiatives from one pool, not a single fixable bug. Unlocking it for real
    would need staged/sequenced investment (e.g. explicitly hold SW's purchase price plus its
    first N days of seed costs in reserve, separate from the operating pool, before buying at
    all) rather than another threshold or reordering tweak.
  - **The real architectural lesson, found by re-reading the #1 team's replay for their actual
    buildup ORDER rather than their end-state composition: they run multiple income streams in
    PARALLEL from day 0, not staged sequentially the way this agent does (wheat alone, then melon
    at day 5, then animals, then -- attempted -- land later).** Their own `PLANT` action log shows
    melon planted days 0-1, right alongside wheat, not gated behind any establishment period --
    the day-5 gate this agent used was our own invented assumption, not something the #1 team
    actually does. Every SW-land scale-up failure above was really a symptom of this: bolting a
    new initiative onto an economy that's *already fully committed* to what's already running,
    late, with no slack left, is fragile in a way that starting multiple things together from day
    0 (when there's naturally less already competing for actor-turns and cash) is not. Tested the
    cheapest version of this insight in isolation: un-gated melon's lower bound (`melon_wants_more
    = obs["day"] <= MELON_LAST_PLANT_DAY`, dropping the old `WHEAT_MAX_YIELD_DAY <` floor) so it
    plants in parallel with wheat from day 0 instead of waiting until day 5 -- safe to test alone
    because melon doesn't carry wheat's starvation risk the way animals do (see wheat_flowing
    below), so none of the animal-feeding logic needed to change. Validated over 40 trials:
    34,067.4/33,106.1 avg reward (up from 31,536.5/32,185.9), 40W-0L both baselines -- a clear,
    genuine win, and the first successful "add something earlier" change after six failed
    "add something new later" attempts. Confirms the staged-buildup assumption itself, not any
    single crop/land/cash parameter, was the actual bottleneck for this lever.
  - **Added CARROT as a cheap endgame crop, and fixed a real gap this exposed in our OWN code
    (not something copied) -- nothing here ever stopped planting wheat near season end.** The #1
    team's replay shows carrot planted only very late (days 25-26) -- a short one-time cycle
    (`max_yield_day` 3, vs. wheat's 4) that still completes even planted almost right up to the
    30-day season's end. Checking our own code found it has zero season-end awareness at all: it
    keeps planting wheat every turn right up to the literal last one, even though a wheat seed
    planted after day `SEASON_LAST_DAY - WHEAT_MAX_YIELD_DAY` (25) can never reach its yield-peak
    age before the season ends -- wasted seed cost and actor-turns on a planting that won't
    finish. Fixed both together: once wheat's own window closes (`wheat_window_open` false),
    remaining empty land redirects to carrot instead (`carrot_window_open`, true through day 26)
    rather than continuing to plant unfinishable wheat or sitting idle; past day 26, nothing
    plants there at all rather than wasting more seed money. Validated over 40 trials:
    34,148.9/34,046.9 avg reward (up from 34,067.4/33,106.1), 40W-0L both baselines -- a further,
    if more modest, genuine win stacked on top of the day-0-melon fix.
  - **Tried and reverted: buying animals from day 0, matching the #1 team's replay exactly (COW
    and SHEEP bought within their first two hours of day 0).** This was previously impossible
    without touching wheat's harvest timing: every harvest waits for the yield peak
    (`WHEAT_MAX_YIELD_DAY`, day 4) for a ~20% reward win found early in this project, meaning
    literally zero wheat exists anywhere before day 4 -- an animal missing 2 consecutive feedings
    escapes for good, so day-0 animals were guaranteed to starve. Added a narrow bootstrap
    exception instead of reverting the peak-timing globally: when zero wheat exists anywhere
    (shed or any actor's inventory) AND at least one animal needs feeding today
    (`wheat_bootstrap_needed`), take one early harvest at `WHEAT_FIRST_YIELD_DAY` (age 2, the
    engine's own earliest-legal-harvest age) instead of waiting for the peak -- smaller yield on
    that one tile, but enough to generate feed in time. Confirmed via replay this technically
    works (the bootstrap harvest fires and animals get fed early), but the OVERALL result is
    still a severe regression: 20,309.7 avg reward (down from 34,046.9) vs `starter`, still 0
    losses but real damage. Root cause, from the same replay: cows and sheep still died and got
    rebought repeatedly all game (`PASTURE+COW` oscillating 1->4->1->2->5->6...) and weeds spiked
    to 30 tiles by day 10-12 -- not a feed-timing bug, but the same actor-capacity problem seen in
    every SW-land attempt: building a pasture, buying and placing two animal types, AND
    establishing wheat all compete for the same single farmer on day 0 (hands don't exist yet),
    which is worse contention than day-0 wheat+melon (a plant just needs watering, not also
    building/buying/placing machinery). The #1 team can pull this off because -- per their own
    replay -- they're not solely dependent on one farmer that early either, or their sequencing of
    which specific action happens which hour absorbs the contention in a way this agent's
    tier-priority-per-turn model doesn't. Reverted entirely (wheat_flowing back to
    `obs["day"] > WHEAT_MAX_YIELD_DAY`, bootstrap-harvest code removed rather than left as unused
    surface area). Confirms day-0 parallel-start generalizes to *plant* additions (melon) but not
    to animals, whose placement machinery costs meaningfully more actor-turns per unit added.
  - **Tried and reverted (seventh SW attempt): SW routed to plain wheat, combined with the
    hire-priority fix for the first time.** Every earlier SW-alone attempt predates the
    hire-priority fix; the hire-priority-fix retry used SW+strawberry, not wheat -- this specific
    combination (simplest possible: SW just becomes more wheat land, `MAX_HANDS` 10->14) had never
    actually been tested, and the base economy is meaningfully stronger now (day-0 melon, carrot,
    routed fertilizer). Result: still a regression, ~30,364/~30,656 avg reward (down from
    34,067.4/34,046.9), though notably less severe than the ~25-26k range every previous SW
    attempt landed at. Replay showed genuine progress on the previously-diagnosed bug -- hand
    count was now stable all game (6-10 range, no more 0-3 crashes) -- but a *different*,
    deeper limit took over: hands never exceeded 10 even with `MAX_HANDS=14`, and weeds/empty
    tiles stayed persistently high (17-23 weeds, 19-35 empty tiles from day 16-28). This isn't a
    cash-flow-timing bug anymore, it's the plain economics from the very first MAX_HANDS comment
    in this file: a hand tending wheat is worth ~$90-100/day (5 tiles x $20/tile/day per the
    README's own Wheat yield/tile/day figure), while the 11th-14th hands cost $89-377/day
    (Fibonacci) -- more wheat land doesn't change that per-hand math at all, so hiring naturally
    plateaus around 9-10 regardless of how much wheat-only land exists to tend, leaving SW's extra
    25 tiles chronically under-cultivated. The fix isn't cash flow (already solved) or hand count
    (already raised) -- it's that WHEAT specifically can't generate enough $/tile/day to justify
    paying for hand 11+. Suggests the next hypothesis: pair new land with a *higher-margin* crop
    (melon's 10x price premium over wheat) rather than more wheat or a similar-maintenance crop
    like strawberry, so the extra hands' cost is justified by what they're tending, not just how
    much land exists. Not yet tried in this specific form (previous melon-scale-up attempts
    predate day-0 melon timing and competed with wheat on the SAME NW/NE land in the SAME narrow
    week, rather than getting new, otherwise-idle SW land to itself).
  - **`MELON_TARGET` 6 -> 12 is the single biggest win found so far: +25% avg reward (34.1k ->
    42.8k), 40W-0L.** Swept 6/10/12/15 = 34.1k / 37.1k / 42.8k / 36.5k, so 12 is a genuine peak
    with the curve falling off on both sides. **The important part is that this exact change was
    already tested and documented as a clear regression earlier** (target 10 lost to 6 at
    ~13,300/~12,900) -- that verdict was simply stale, because it predated both day-0 melon
    planting and the hire-priority fix. At the old day-5-7 window the extra melon tiles collided
    with that week's first animal purchases for the same actor-turns; planted from day 0 they have
    the empty board to themselves. Two lessons worth carrying: (a) re-test tuning verdicts whenever
    a structural fix lands underneath them, because "known bad" numbers can silently become good,
    and (b) this independently converged on the #1 team's own figure -- their replay peaks at
    exactly 10 simultaneous melon tiles, right next to our measured optimum of 12.
  - **What the #1 team's revenue actually comes from** (summing every SELL order in their replay
    against the price at time of sale): strawberry ~44,800, wheat ~21,700, wool ~18,500, milk
    ~17,100, **fertilizer ~13,600**, egg ~12,800, melon ~12,500, tomato ~900, carrot ~600 —
    ~142,400 gross. Worth noting against our own priorities: animal products together (~48,400)
    outweigh their biggest crop; they *sell* fertilizer for real money where we only spend ours on
    wheat; their egg income comes from 9 geese where `ANIMAL_PLANS` has goose dialed to 0; and
    tomato/carrot are rounding errors for them too, matching our own findings on both.
  - **Tried and reverted: strawberry, third attempt — and the ramp finding is the valuable part.**
    Strawberry is the #1 team's single biggest earner (~44,800, above), so it was worth retrying
    with every earlier failure mode addressed: persistent land reservation (no narrow window, so no
    seed-stranding), day-0 availability, no confounding land purchase, and — new — fertilizer
    extended to it, since the engine doubles each scheduled production 1 -> 2 when a tile is both
    watered and fertilized that day, and the #1 team's ~44,800 at their realised ~$150/unit implies
    ~300 units from 37 tiles where 4 productions each would only give 148 unfertilized. First run at
    `STRAWBERRY_TARGET=12` was a catastrophic ~19.8k. Replay found the cause immediately and it was
    not strawberry's economics: claiming all 12 tiles at once put 12 x $100 of seed on top of
    melon's 12 x $80 within the first two turns, money crashed $3000 -> $51 by day 2, and 24 fresh
    tiles all needing daily water at once ran weeds to 28 by day 6, collapsing wheat from 16 tiles
    to 0. Adding a gradual ramp (1 tile/day from day 2, mirroring the #1 team's own ~3/day over
    days 2-15) recovered it to ~37,058/~37,164 -- an enormous rescue, but still ~5.7k below the
    melon-only 42.8k baseline. Halving the target to 6 improved it again to ~38,354/~38,393, still
    ~4.4k short, so it's a regression at every size tested rather than just mis-tuned. The gap
    looks like plain land budget: 12 melon + 12
    strawberry + 9 animal structures is 33 of our 50 tiles, leaving ~17 for wheat, which is also
    the animals' feed supply; the #1 team affords 37 strawberry tiles because they have 75 tiles to
    work with. Reverted to melon-only. The ramp mechanism itself is the transferable lesson -- any
    future multi-tile crop addition should phase its land claim in over days rather than reserving
    the whole target on day 0, and the full ramped implementation is saved as a patch at
    `scratchpad/strawberry_ramped.patch` for when land expansion is solved.
  - Also checked hand-hiring for a market-adaptivity angle: an early read of the #1 team's
    replay, sampled only at hour 0 each day, misleadingly showed 0 hands every single day —
    turns out hire contracts expire and must be re-bought every day at hour 0, so hour-0 is
    always mid-reset. Sampling any other hour shows them actually scaling 4→12 hands over the
    season, tracking their land/animal growth. No actionable difference found here beyond what
    `MAX_HANDS`/`TILES_PER_ACTOR` already does.
- Not yet using: SW/SE land (see above), strawberry/tomato, a bigger animal herd matching the #1
  team's ~18-23, tighter cash management, a higher-priority fertilizer tier (see above), or a hand
  wind-down near season end (the studied opponent had 0 hands by day 29 -- carrot now covers the
  crop side of their endgame wind-down, see above, but hiring itself still runs at full size right
  to the last day here, which may be paying for hands with nothing left worth doing that late).
  Further layers should be validated the same way this round was — replay-inspected, not just
  win/loss, one variable at a time — since the real leaderboard (thousands of tuned competitor
  bots, currently ranking us ~7000th of ~9700 at a 491 score vs. leaders around 3000) is a much
  higher bar than these two fixed baselines.
- **Tried and reverted (first attempt): spending fertilizer on wheat.** Engine confirms
  `FERTILIZE` raises wheat's max yield 4→6 (worth doing), and it's free — collected off animals
  via `COLLECT_FERTILIZER` (at the time, only implemented as an idle-time bonus action, tier 8).
  But adding a tier to actually spend it on wheat moved the benchmark by less than trial-to-trial
  noise (~21,600/~21,600 either way over 40 trials, vs. ~21,421/~21,900 without it). Replayed a
  full 720-turn game to find out why instead of guessing: only 3 `COLLECT_FERTILIZER` calls and
  **zero** `FERTILIZE` calls fired in the whole game. Root cause: collection was gated on an actor
  being *fully idle* standing on the animal's own tile, which with ~9 hands covering ~50 tiles
  almost never happens — there's always a higher-priority task. The fertilizer-spending logic
  itself was never wrong, it just never had any fertilizer to spend. Reverted rather than keep
  dead code, with the diagnosis (needs its own routed tier, not idle-fallback) written down as the
  fix for later — see the successful second attempt above, which did exactly that after the #1
  team's own replay confirmed the lever was worth revisiting.

## Testing before submitting

Always run `python run_match.py random --trials 20` and `python run_match.py starter --trials 20`
after a strategy change. Only submit via `kaggle competitions submit` when both show a clear
positive win rate — submissions are rate-limited, so don't burn one to test something local
testing can already answer.
