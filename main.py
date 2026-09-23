"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Strategy: a multi-tile, multi-actor wheat farm (farmer + hired hands) that
also runs a livestock operation (cows and sheep sharing pasture -- geese
support still exists in the code but is dialed to 0, see ANIMAL_PLANS)
fed from its own wheat surplus, plus a small one-shot melon batch (see
MELON_TARGET) for market diversification, planted in PARALLEL with wheat
from day 0 (matching the #1 team's own replay -- melon doesn't share
wheat's starvation risk, only animals do). Animal purchases are gated
until wheat is actually flowing (see wheat_flowing below) -- replay
analysis showed an animal bought on day 0 has no feed source at all and
is guaranteed to starve within 2 days.

Every turn, every active unit is greedily matched to the nearest task in
this priority order: feed unfed animals (losing one to starvation costs
far more than delaying anything else by a turn) > harvest (crops and
animal products) > water thirsty plants > fertilize a wheat tile still in
its watering-bonus window, using fertilizer already carried > place a
purchased animal into its empty structure > build new structures > plant
wheat, then melon, on the rest of the land > collect fertilizer from fed
animals, for a future turn's fertilize tier to spend > clear weeds to
reclaim tiles. Fertilizer collection sits just above weeding rather than
above placing/building/planting: an earlier attempt at promoting it that
high was a clear regression (diverting too many actor-turns from tasks
with a hard deadline), since collected fertilizer itself never expires if
left uncollected a while. Actors idle on a useful tile opportunistically
CARE for a fed animal.

Key mechanics this relies on (confirmed against the installed
kaggle_environments source, not just the README): FEED and PLACE consume
items from the *acting unit's own inventory*, not the shared shed, so
geese/wheat must be PICKUP'd from the shed before they can be used --
BUILD_COOP/BUILD_PASTURE cost zero gold, only one action.

Harvests are timed to wheat's yield peak (day 4) rather than the first
eligible day (day 2), since HARVEST costs the same one turn either way.

Hiring is sized and its cost reserved from the shared cash pool FIRST,
before any seed/animal/land purchase gets a chance to spend into it (the
actual HIRE orders still queue last in the market list -- a separate,
order-count-truncation concern, not a cash-priority one). A repeated
failure mode across several scale-up experiments (see CLAUDE.md "Copying
the #1 team's strategy") traced back to sizing hire last against whatever
cash survived other purchases, letting a big same-turn buy crash hand
count with no protection.

See AGENTS.md / README.md for full rules and CLAUDE.md for the testing
workflow.
"""

WHEAT_SEED_COST = 10
WHEAT_MAX_YIELD_DAY = 4
# Only NE is bought: MAX_HANDS=10 caps us at 11 actors, which at
# TILES_PER_ACTOR=5 can realistically staff ~55 tiles -- almost exactly
# NW+NE (50). SW/SE would cost $6000 combined for land we can't staff any
# better than what we already have, per replay (tile utilization doesn't
# improve past ~2 quadrants at this actor cap).
#
# Tried buying SW several ways -- routed to wheat, to a dedicated
# strawberry batch, with more hands, with hire's cost reserved first, with
# SW gated behind a much higher cash floor -- all regressed (see CLAUDE.md
# "Copying the #1 team's strategy" for the full six-attempt diagnosis).
# The bottleneck is fundamentally economic: funding land + 3 crops +
# animals from one shared cash pool during the establishment period
# outspends what the base economy generates, and no reordering or
# threshold tweak fixes a genuinely over-committed budget. Left at NE-only
# until a real fix (staged/sequenced investment, not a bigger threshold)
# is attempted.
LAND_COSTS = {"NE": 1000}
# Tried dropping this to 5 (matching the #1 team's own near-$0 daily
# balances -- see CLAUDE.md "Copying the #1 team's strategy") and it was a
# clear regression: hand count went erratic (0-1 hands on several days,
# vs. a steady ~7-9), and melon's 6 bought seeds were never planted the
# entire game. Root cause: HIRE is deliberately sized last against
# whatever `available` cash survives seed/animal/land purchases (see
# "Shared cash pool" below) -- with only a $5 buffer, those purchases
# regularly spend it all, crashing hand count on exactly the days melon's
# narrow window needs actor-turns most. The #1 team can run this lean
# because hiring isn't competing with a hard-gated one-shot planting
# window the way ours is; copying their cash aggression here needs hire
# to have its own reserved floor first, not just a smaller number.
CASH_RESERVE = 50
MAX_SEED_STOCKPILE = 30

# Step 2: add MELON alone (no strawberry/tomato yet) as a small, one-shot
# batch, never replanted after (matches the #1 team's own pattern: their
# 12-day max_yield_day plus a punishing glut curve on oversupply make a
# second full cycle not worth the market risk). Melon's own watering-bonus
# window works exactly like wheat's (both are one-time crops), just with
# its own max_yield_day -- see the classification loop below, generalized
# to check crop type rather than assuming wheat.
#
# Originally gated behind WHEAT_MAX_YIELD_DAY (day 5+) on the theory that
# wheat needed to "establish" first -- re-examining the #1 team's own
# replay (see CLAUDE.md "Copying the #1 team's strategy") showed they
# actually plant melon on days 0-1, in PARALLEL with wheat, not staged
# after it. Melon doesn't carry wheat's starvation risk (only ANIMALS do,
# see wheat_flowing below) -- a plant just sits there needing water, so
# there's no survival reason to delay it. Un-gating the lower bound to
# test their actual timing rather than our own assumption about it.
#
# MELON_TARGET swept 6 -> 10 -> 12 -> 15 once that day-0 timing landed:
# 34.1k / 37.1k / 42.8k / 36.5k avg reward respectively, so 12 is the
# peak and the curve is non-monotonic on both sides of it (same
# cliff/chaos shape this agent's capacity curves always have -- see
# ANIMAL_PLANS). An earlier sweep found 10 a clear REGRESSION vs 6, but
# that predated day-0 planting and the hire-priority fix: at day 5-7 the
# extra tiles collided with that week's animal purchases for the same
# actor-turns, whereas from day 0 they have the whole empty board to
# themselves. Worth re-testing any "already known" tuning verdict that
# was established before a structural fix like that one.
MELON_SEED_COST = 80
MELON_MAX_YIELD_DAY = 12
MELON_TARGET = 12
MELON_LAST_PLANT_DAY = 7

# Step 4: an endgame crop. The #1 team plants CARROT only very late (days
# 25-26 in their replay) -- a short one-time cycle (max_yield_day 3, vs.
# wheat's 4) that still completes before the 30-day season ends even when
# planted almost right up to the end. This exposed a real gap in our OWN
# code, not a copied trick: nothing here ever stopped planting wheat near
# season end, so a wheat seed planted after day SEASON_LAST_DAY -
# WHEAT_MAX_YIELD_DAY (25) can never reach its yield-peak age before the
# season is over -- wasted seed cost and actor-turns on a planting that
# won't finish. Once wheat's own window closes, land that's still empty
# switches to carrot instead (whose own, later-closing window still fits)
# rather than sitting idle or wasting more wheat seed -- see wheat_window_
# open/carrot_window_open below.
SEASON_LAST_DAY = 29
CARROT_SEED_COST = 20
CARROT_MAX_YIELD_DAY = 3

# Hire cost is Fibonacci per hand per day (1,1,2,3,5,8,13,21,34,55,89,144...).
# A hand tending ~TILES_PER_ACTOR wheat tiles is worth roughly $90-100/day
# gross, so the 11th hand (cost 144) is the first one that's a net loss --
# cap just below that. Replay analysis showed the old cap of 4 left 40-60
# of 100 owned tiles idle once all land was bought, since actor count never
# scaled past the starting quadrant's needs.
MAX_HANDS = 10
TILES_PER_ACTOR = 5

# Priority order matters: earlier plans get first pick of nearby empty land,
# first claim on spare cash, and (for plans sharing a structure) first claim
# on newly-built structures of that type -- see structure_plans below.
#
# A leaderboard replay of a top opponent (118k final money vs our 21k with
# the original 2 cow / 4 goose pilot) showed them running 9 cows + 4 sheep
# and no geese at all -- motivating both adding sheep and dropping goose
# (weakest $/day of the three: ~$50 vs cow's ~$80 and sheep's ~$67).
# Getting there took several replay-guided rounds, each testing ONE
# variable at a time since this game's actor-turn economy makes herd size
# a genuinely non-monotonic function of reward, not a dial that just goes up:
#   - 8 cow + 5 sheep + 6 goose (19 total structures) reserved that much
#     land/actor-turns from turn 0, before any wheat income existed --
#     death spiral (weeds compounded, wheat capacity collapsed 46->4 tiles,
#     herd starved 14->1 animals). Building structures incrementally
#     instead of all at once for large targets is still worth revisiting,
#     but was dropped here in favor of simply sizing targets to what the
#     current actor count can sustain.
#   - Dropping to 4 cow + 2 sheep + 4 goose still underperformed the
#     original pilot -- turned out to be a separate bug (see structure_plans:
#     an empty pasture was earmarked by overall-target distance, so cow's
#     large target starved sheep of ever being placed) plus an incremental-
#     build throttle that (harmlessly for cow/sheep, but not for goose)
#     delayed reaching even a small target. Both fixed in code, not by
#     retuning numbers.
#   - With those fixed: 6 cow + 3 sheep + 0 goose beat the original pilot by
#     ~20%, and higher still (9+4) works locally too -- but 8+3 (11 total)
#     collapsed WORSE than 9+4 (13 total) did, i.e. reward is not monotonic
#     in headcount near this actor count's ceiling. Settled here rather
#     than chase that cliff further; raising it again needs more hands
#     and/or a lower TILES_PER_ACTOR to go with it, re-validated by replay
#     for the same collapse pattern each time, not just win/loss.
ANIMAL_PLANS = [
    {"animal": "COW", "structure": "PASTURE", "cost": 400, "target": 6},
    {"animal": "SHEEP", "structure": "PASTURE", "cost": 500, "target": 3},
    {"animal": "GOOSE", "structure": "COOP", "cost": 300, "target": 0},
]
BUILD_OP = {"PASTURE": "BUILD_PASTURE", "COOP": "BUILD_COOP"}
# Animals sit in the shed (via BUY_ANIMAL) awaiting PICKUP+PLACE just like any
# other shed item, but the engine's SELL only accepts real products -- a
# SELL order for one of these is a silent no-op that still burns one of the
# 10 market-order slots for the turn. Keep them out of the shed-surplus loop.
ANIMAL_NAMES = {p["animal"] for p in ANIMAL_PLANS}


def _fib_hire_cost(n):
    """Cost of the (n+1)-th hire today, n = hires already made today."""
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def _step_toward(fx, fy, tx, ty):
    if fx < tx:
        return "EAST"
    if fx > tx:
        return "WEST"
    if fy < ty:
        return "SOUTH"
    if fy > ty:
        return "NORTH"
    return None


def _manhattan(fx, fy, tx, ty):
    return abs(fx - tx) + abs(fy - ty)


def _own_tiles(farm):
    """(x, y, tile) for every tile not in a locked quadrant."""
    tiles = []
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile != "LOCKED":
                tiles.append((x, y, tile))
    return tiles


def _shed_access_tiles(board_size):
    half = board_size // 2
    return [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]


def agent(obs):
    me = obs["farms"][obs["player"]]
    private = obs["private"]
    board_size = len(me["tiles"])
    tiles = _own_tiles(me)

    actor_positions = [tuple(me["farmer"])] + [tuple(h) for h in me["hands"]]
    inventories = private["inventories"]
    ops = [None] * len(actor_positions)
    unassigned = list(range(len(actor_positions)))

    shed = private["shed"]
    seeds = private["seeds"]
    market = []

    # ---- Sell shed surplus, keeping enough wheat to feed the animals ----
    # Animals awaiting PICKUP+PLACE also sit in the shed; excluded here since
    # the engine's SELL silently no-ops on a non-product item but still
    # burns one of the turn's 10 market-order slots getting there.
    placed_animals = sum(1 for _, _, t in tiles if isinstance(t, dict) and "animal" in t)
    for item, count in shed.items():
        if item in ANIMAL_NAMES:
            continue
        sellable = max(0, count - placed_animals) if item == "WHEAT" else count
        if sellable > 0:
            market.append(["SELL", item, sellable])

    # ---- How many of each animal do we already have, one count reused below
    # for both buy-gating and structure/placement decisions ----
    placed_by_animal = {}
    for _, _, t in tiles:
        if isinstance(t, dict) and "animal" in t:
            placed_by_animal[t["animal"]] = placed_by_animal.get(t["animal"], 0) + 1
    animal_owned = {
        p["animal"]: (
            shed.get(p["animal"], 0)
            + sum(inv.get(p["animal"], 0) for inv in inventories)
            + placed_by_animal.get(p["animal"], 0)
        )
        for p in ANIMAL_PLANS
    }

    # ---- Decide how much land is wheat vs. reserved for new structures ----
    # Cow and sheep share PASTURE, so the land reserved for a structure is
    # the SUM of every plan targeting it, not each plan's target taken in
    # isolation (which would double-book the same tiles against both).
    empty_tiles = [(x, y) for x, y, t in tiles if t is None]
    shed_center = (board_size // 2, board_size // 2)
    empty_by_dist = sorted(empty_tiles, key=lambda t: _manhattan(t[0], t[1], *shed_center))

    structure_targets = {}
    for plan in ANIMAL_PLANS:
        structure_targets[plan["structure"]] = structure_targets.get(plan["structure"], 0) + plan["target"]

    build_targets = []  # (x, y, "BUILD_COOP" | "BUILD_PASTURE")
    for structure, target in structure_targets.items():
        existing = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == structure)
        slots_wanted = max(0, target - existing)
        claimed, empty_by_dist = empty_by_dist[:slots_wanted], empty_by_dist[slots_wanted:]
        build_targets.extend((x, y, BUILD_OP[structure]) for x, y in claimed)

    # Melon claims its small slice before wheat gets the remainder -- see
    # MELON_TARGET above for why this is gated and capped so small.
    melon_existing = sum(
        1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == "PLANT" and t.get("crop") == "MELON"
    )
    melon_wants_more = obs["day"] <= MELON_LAST_PLANT_DAY
    melon_target = MELON_TARGET if melon_wants_more else melon_existing
    melon_slots = max(0, melon_target - melon_existing)
    melon_targets, empty_by_dist = empty_by_dist[:melon_slots], empty_by_dist[melon_slots:]

    # Remaining land defaults to wheat while its window is still open; once
    # a wheat seed planted today could no longer reach its yield-peak age
    # before the season ends, redirect to carrot instead (whose shorter
    # cycle still fits) rather than planting wheat that can't finish or
    # leaving the land empty. See SEASON_LAST_DAY above.
    wheat_window_open = obs["day"] <= SEASON_LAST_DAY - WHEAT_MAX_YIELD_DAY
    carrot_window_open = obs["day"] <= SEASON_LAST_DAY - CARROT_MAX_YIELD_DAY
    if wheat_window_open:
        plant_targets, carrot_targets = empty_by_dist, []
    elif carrot_window_open:
        plant_targets, carrot_targets = [], empty_by_dist
    else:
        plant_targets, carrot_targets = [], []

    # ---- Shared cash pool for everything this turn spends. Each section
    # below must decrement the SAME running `available` the next section
    # reads, not a separate, non-decremented copy of it.
    available = me["money"] - CASH_RESERVE

    # ---- Hire hands for the day FIRST, reserving their cost out of
    # `available` before seed/animal/land purchases get a chance to spend
    # it -- the actual HIRE orders are still appended to `market` LAST (see
    # below), which is a separate concern (maxMarketOrdersPerTurn (10)
    # truncates the raw order LIST, so losing a hire for one day there is
    # cheaper than losing a sell/buy). This fixes a repeated failure mode
    # (see CLAUDE.md "Copying the #1 team's strategy"): sizing hire against
    # whatever cash survived other purchases meant a big same-turn land or
    # seed purchase could crash hand count for the day with no warning --
    # confirmed via replay in both the cash-aggression and SW+strawberry
    # scale-up attempts, where hand count fell to 0-3 on turns big
    # purchases landed. Reserving hire's cost first makes hand count stable
    # regardless of what else this turn wants to spend on.
    hire_orders = []
    if obs["hour"] == 0:
        desired_hands = min(MAX_HANDS, max(0, len(tiles) // TILES_PER_ACTOR - 1))
        n_hire, spent = 0, 0
        while n_hire < desired_hands:
            cost = _fib_hire_cost(n_hire)
            if spent + cost > available:
                break
            spent += cost
            n_hire += 1
        hire_orders = [["HIRE"] for _ in range(n_hire)]
        available -= spent

    # ---- Keep enough wheat seed for every tile we intend to plant ----
    seeds_owned = seeds.get("WHEAT", 0)
    target_seeds = min(len(plant_targets), MAX_SEED_STOCKPILE)
    to_buy = max(0, min(target_seeds - seeds_owned, available // WHEAT_SEED_COST))
    if to_buy > 0:
        market.append(["BUY_SEED", "WHEAT", to_buy])
        available -= to_buy * WHEAT_SEED_COST

    # ---- Keep enough melon seed for its (small, one-shot) reserved land ----
    melon_seeds_owned = seeds.get("MELON", 0)
    melon_to_buy = max(0, min(len(melon_targets) - melon_seeds_owned, available // MELON_SEED_COST))
    if melon_to_buy > 0:
        market.append(["BUY_SEED", "MELON", melon_to_buy])
        available -= melon_to_buy * MELON_SEED_COST

    # ---- Keep enough carrot seed for the endgame land carrot_window_open
    # redirected to it (see above) -- naturally 0 before that window opens,
    # since carrot_targets is empty until then.
    carrot_seeds_owned = seeds.get("CARROT", 0)
    carrot_to_buy = max(0, min(len(carrot_targets) - carrot_seeds_owned, available // CARROT_SEED_COST))
    if carrot_to_buy > 0:
        market.append(["BUY_SEED", "CARROT", carrot_to_buy])
        available -= carrot_to_buy * CARROT_SEED_COST

    # ---- Buy animals up to each plan's target, once wheat is flowing ----
    # Wheat harvests are deliberately delayed until WHEAT_MAX_YIELD_DAY (see
    # below), so an animal bought before then has no feed source at all and
    # is guaranteed to starve and escape within its first 2 days -- burning
    # the full purchase cost for nothing. Confirmed via replay: geese bought
    # on day 0 died by day 2-3 every time before this gate was added.
    wheat_flowing = obs["day"] > WHEAT_MAX_YIELD_DAY
    if wheat_flowing:
        for plan in ANIMAL_PLANS:
            animal = plan["animal"]
            if animal_owned[animal] < plan["target"] and plan["cost"] <= available:
                market.append(["BUY_ANIMAL", animal, 1])
                available -= plan["cost"]

    # ---- Expand land once comfortably affordable ----
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            available -= cost
            break

    # Hiring goes last IN THE LIST (its cost was already reserved above):
    # maxMarketOrdersPerTurn (10) truncates the market list,
    # and losing a hand-hire for one day is far cheaper than losing a wheat
    # sale or animal purchase. Replay showed a 9-hand hire burst plus a sell
    # and two buys hit 12 orders on one turn -- with hire queued first, the
    # sell and both buys were silently dropped.
    market.extend(hire_orders)

    # ---- Build per-tile task tiers ----
    # Cow and sheep share PASTURE. Earmarking an empty pasture for whichever
    # shared plan is furthest from its overall TARGET (an earlier version of
    # this) is a trap: cow's target (8) is big enough that it rarely
    # finishes, so every empty pasture kept getting earmarked "COW" even on
    # turns where a SHEEP was the one actually sitting in the shed ready to
    # go -- confirmed via replay: all 5 target sheep got bought (correctly
    # capped at target) but sat dead in the shed the entire game, since no
    # actor could ever satisfy PLACE COW there. Deciding the animal at
    # PLACE time instead, from what's actually on hand, avoids this.
    structure_plans = {}
    for plan in ANIMAL_PLANS:
        structure_plans.setdefault(plan["structure"], []).append(plan)

    # Studying the #1 team's own replay (see CLAUDE.md "Copying the #1
    # team's strategy") showed 490 COLLECT_FERTILIZER + 237 FERTILIZE calls
    # over 720 turns -- a dedicated, routed habit, not a rare idle-time
    # bonus. Confirmed against the engine: FERTILIZE sets
    # fertilized_until_day = day+2 (active for day, day+1, day+2), and for a
    # one-time crop, WATER's own yield bonus doubles (1 -> 2) on any day that
    # condition holds. Wheat's watering-bonus window is exactly 3 days
    # (age 2-4), so ONE FERTILIZE call at age 2 covers the whole window and
    # lifts wheat's realistic cap from 4 (watering alone) to its true max of
    # 6 -- a 50% yield boost from a byproduct our own animals already make
    # for free. Melon is deliberately excluded: its window is 7 days (age
    # 6-12) but watering alone already reaches its cap of 6 by age 10 (see
    # README), so spending fertilizer there is wasted.
    WHEAT_FERTILIZE_WINDOW_START = (WHEAT_MAX_YIELD_DAY + 1) // 2

    harvest_targets, feed_targets, water_targets, place_targets, weed_targets = [], [], [], [], []
    fertilize_targets, collect_fertilizer_targets = [], []
    for x, y, tile in tiles:
        if isinstance(tile, dict):
            if tile.get("kind") == "PLANT":
                age = obs["day"] - tile["planted_day"]
                # Melon is also a one-time crop with the exact same
                # watering-bonus-window shape as wheat, just its own
                # max_yield_day -- generalized here rather than duplicating
                # wheat's block. Same for carrot (see SEASON_LAST_DAY above).
                if tile["crop"] == "MELON":
                    max_yield_day = MELON_MAX_YIELD_DAY
                elif tile["crop"] == "CARROT":
                    max_yield_day = CARROT_MAX_YIELD_DAY
                else:
                    max_yield_day = WHEAT_MAX_YIELD_DAY
                # The watering bonus window is inclusive of max_yield_day
                # itself (engine: window_start <= age <= max_yield_day), so a
                # plant at exactly that age still needs watering today before
                # harvest -- skipping straight to harvest here silently drops
                # one yield unit (of 4) on every single wheat cycle.
                if tile["yield_units"] > 0 and (
                    age > max_yield_day
                    or (age == max_yield_day and tile["watered_today"])
                ):
                    harvest_targets.append((x, y))
                elif not tile["watered_today"] and age <= max_yield_day:
                    water_targets.append((x, y))
                # `fertilized_until_day < WHEAT_MAX_YIELD_DAY` means this
                # tile's coverage (if any) doesn't yet reach the window's
                # last day -- re-targets a tile fertilized too early without
                # re-spending on one already fully covered.
                if (
                    tile["crop"] == "WHEAT"
                    and WHEAT_FERTILIZE_WINDOW_START <= age <= WHEAT_MAX_YIELD_DAY
                    and tile.get("fertilized_until_day", -1) < WHEAT_MAX_YIELD_DAY
                ):
                    fertilize_targets.append((x, y))
            elif "animal" in tile:
                if tile["yield_units"] > 0:
                    harvest_targets.append((x, y))
                if not tile["fed_today"]:
                    feed_targets.append((x, y))
                if tile["fertilizer_available"]:
                    collect_fertilizer_targets.append((x, y))
            elif tile.get("kind") in structure_plans:
                place_targets.append((x, y, tile["kind"]))
            elif tile.get("kind") == "WEED":
                weed_targets.append((x, y))

    def assign(targets, immediate_action_fn, feasible=None):
        """Greedily match unassigned actors to targets: immediate action for
        actors already standing on one, else move the nearest remaining
        actor toward the nearest remaining target. Returns leftover targets
        nobody was matched to."""
        remaining = list(targets)
        for ai in list(unassigned):
            pos = actor_positions[ai]
            match = next((t for t in remaining if (t[0], t[1]) == pos), None)
            if match is not None and (feasible is None or feasible(ai, match)):
                ops[ai] = immediate_action_fn(ai, match)
                unassigned.remove(ai)
                remaining.remove(match)
        while unassigned and remaining:
            best = None
            for ai in unassigned:
                ax, ay = actor_positions[ai]
                for t in remaining:
                    if feasible is not None and not feasible(ai, t):
                        continue
                    d = _manhattan(ax, ay, t[0], t[1])
                    if best is None or d < best[0]:
                        best = (d, ai, t)
            if best is None:
                break
            _, ai, t = best
            move = _step_toward(actor_positions[ai][0], actor_positions[ai][1], t[0], t[1])
            ops[ai] = [move] if move else ["PASS"]
            unassigned.remove(ai)
            remaining.remove(t)
        return remaining

    # 1. Feed unfed animals first -- losing one (2 consecutive unfed days)
    #    wastes its full purchase cost and days of lost production, which
    #    costs far more than delaying a harvest by one turn. Only actors
    #    already carrying wheat (from a recent harvest or earlier pickup)
    #    can act immediately; if this tier ran after harvest instead, a
    #    wheat-carrying actor would get redrafted into the next harvest
    #    before ever delivering it, which is exactly how geese starved in
    #    testing. Leftover unfed animals get an idle actor sent to fetch
    #    wheat from the shed for a future turn.
    has_wheat = lambda ai, t: inventories[ai].get("WHEAT", 0) > 0
    unfed_left = assign(feed_targets, lambda ai, t: ["FEED"], feasible=has_wheat)
    if unfed_left and shed.get("WHEAT", 0) > 0:
        # A single actor's inventory can carry the whole shortfall in one
        # trip, then deliver it across several later turns -- so cap this to
        # one fetcher via the shared budget below. Without the feasible gate,
        # assign() can match every actor already standing on a distinct
        # shed-access tile this same turn, and each would get handed the
        # unshared, un-decremented `n`, over-fetching wheat the just-queued
        # SELL WHEAT order above was counting on.
        fetch_budget = [min(len(unfed_left), shed["WHEAT"])]

        def pickup_wheat(ai, t):
            n, fetch_budget[0] = fetch_budget[0], 0
            return ["PICKUP", "WHEAT", n]

        assign(
            _shed_access_tiles(board_size),
            pickup_wheat,
            feasible=lambda ai, t: fetch_budget[0] > 0,
        )

    # 2. Harvest ripe crops and animal products -- banks cash and frees the
    #    tile for the next cycle.
    assign(harvest_targets, lambda ai, t: ["HARVEST"])

    # 3. Water thirsty wheat -- avoid weeds.
    assign(water_targets, lambda ai, t: ["WATER"])

    # 3b. Spend any fertilizer already carried (from tier 7b's collection on
    #     a previous turn -- COLLECT_FERTILIZER lands straight in the acting
    #     unit's own inventory, like HARVEST, so there's no shed round-trip)
    #     on a wheat tile that still needs it this window.
    has_fertilizer = lambda ai, t: inventories[ai].get("FERTILIZER", 0) > 0
    assign(fertilize_targets, lambda ai, t: ["FERTILIZE"], feasible=has_fertilizer)

    # 4. Place a carried animal into its empty structure; if nobody is
    #    carrying one yet, send an idle actor to the shed to pick one up.
    #    Cow and sheep share PASTURE, so which animal a given tile gets is
    #    decided here from what's actually on hand (see structure_plans
    #    above for why deciding it any earlier is a trap).
    def animal_for(ai, structure):
        for plan in structure_plans[structure]:
            if inventories[ai].get(plan["animal"], 0) > 0:
                return plan["animal"]
        return None

    has_animal = lambda ai, t: animal_for(ai, t[2]) is not None
    unplaced_left = assign(place_targets, lambda ai, t: ["PLACE", animal_for(ai, t[2])], feasible=has_animal)

    # Fallback: send an idle actor to fetch whichever shared-structure animal
    # is actually sitting in the shed, for each still-empty structure tile.
    # `shed_stock` decrements locally as tiles claim it (mirroring the
    # wheat-fetch fix above) so two tiles wanting the same animal don't both
    # dispatch a fetcher against the same single unit of stock.
    shed_stock = {p["animal"]: shed.get(p["animal"], 0) for p in ANIMAL_PLANS}
    for _, _, structure in unplaced_left:
        animal = next((p["animal"] for p in structure_plans[structure] if shed_stock[p["animal"]] > 0), None)
        if animal is None:
            continue
        budget = [1]

        def pickup_animal(ai, t, animal=animal, budget=budget):
            budget[0] -= 1
            return ["PICKUP", animal, 1]

        assign(
            _shed_access_tiles(board_size),
            pickup_animal,
            feasible=lambda ai, t, budget=budget: budget[0] > 0,
        )
        # Only decrement once the dispatch actually happened (budget hit 0)
        # -- if no actor was free to send this turn, the unit is still in
        # the shed and a later tile this same turn should still see it.
        if budget[0] == 0:
            shed_stock[animal] -= 1

    # 5. Build new structures on reserved land.
    assign(build_targets, lambda ai, t: [t[2]])

    # 6. Plant wheat on the rest of the empty land (capped by seeds owned --
    #    attempting more simultaneous plants than seeds owned fails all of
    #    them for the turn).
    plant_budget = [seeds_owned]

    def can_plant(ai, t):
        return plant_budget[0] > 0

    def do_plant(ai, t):
        plant_budget[0] -= 1
        return ["PLANT", "WHEAT"]

    assign(plant_targets, do_plant, feasible=can_plant)

    # 6b. Plant melon on its small reserved slice, same idea as wheat above.
    melon_plant_budget = [melon_seeds_owned]

    def can_plant_melon(ai, t):
        return melon_plant_budget[0] > 0

    def do_plant_melon(ai, t):
        melon_plant_budget[0] -= 1
        return ["PLANT", "MELON"]

    assign(melon_targets, do_plant_melon, feasible=can_plant_melon)

    # 6c. Plant carrot on whatever land wheat's own window has closed on
    #     (see SEASON_LAST_DAY above) -- same idea as wheat/melon above.
    carrot_plant_budget = [carrot_seeds_owned]

    def can_plant_carrot(ai, t):
        return carrot_plant_budget[0] > 0

    def do_plant_carrot(ai, t):
        carrot_plant_budget[0] -= 1
        return ["PLANT", "CARROT"]

    assign(carrot_targets, do_plant_carrot, feasible=can_plant_carrot)

    # 6d. Collect fertilizer from fed animals, for a future turn's 3b above
    #     to spend. A first attempt promoted this ahead of place/build/plant
    #     (right after 3b) to fix the original placement's under-volume (see
    #     CLAUDE.md) -- that was a clear regression (vs starter dropped from
    #     19-1 with 23,897 avg reward, one of very few losses this agent has
    #     taken against either baseline), since diverting that many
    #     actor-turns away from watering/harvesting/planting outweighs the
    #     fertilizer upside. Landing it here instead -- after both plantings
    #     but still ahead of weeding -- is a smaller promotion from the
    #     original tier 7b (see CLAUDE.md for that attempt's ~26-collect
    #     under-volume) without competing with anything that has a hard
    #     deadline (feed/water) or claims land/cash (place/build/plant).
    assign(collect_fertilizer_targets, lambda ai, t: ["COLLECT_FERTILIZER"])

    # 7. Clear weeds to reclaim the tile for future planting. Low urgency
    #    (no immediate payoff), but left unchecked these compound: replay
    #    analysis showed 31 of 100 owned tiles lost to weeds by day 29 with
    #    no clearing at all.
    assign(weed_targets, lambda ai, t: ["DIG"])

    # 8. Opportunistic bonus for actors with nothing better to do this turn:
    #    care for a fed animal (banks a yield bonus for its next production).
    for ai in list(unassigned):
        x, y = actor_positions[ai]
        tile = me["tiles"][y][x]
        if isinstance(tile, dict) and "animal" in tile:
            if not tile["cared_today"] and tile["fed_today"]:
                ops[ai] = ["CARE"]
                unassigned.remove(ai)

    for ai in unassigned:
        ops[ai] = ["PASS"]

    return {"farmer": ops[0], "hands": ops[1:], "market": market}
