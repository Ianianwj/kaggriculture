"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Strategy: a multi-tile, multi-actor wheat farm (farmer + hired hands) that
also runs a small livestock operation (cows, then geese) fed from its own
wheat surplus. Cows are prioritized over geese: milk nets roughly double
the daily profit per unit of wheat spent feeding it, even though cows cost
more up front and take longer to mature. Animal purchases are gated until
wheat is actually flowing (see wheat_flowing below) -- replay analysis
showed an animal bought on day 0 has no feed source at all and is
guaranteed to starve within 2 days, burning its full purchase cost.

Every turn, every active unit is greedily matched to the nearest task in
this priority order: feed unfed animals (losing one to starvation costs
far more than delaying anything else by a turn) > harvest (crops and
animal products) > water thirsty wheat > place a purchased animal into
its empty structure > build new structures > plant wheat on the rest of
the land > clear weeds to reclaim tiles. Actors idle on a useful tile
opportunistically CARE for a fed animal or collect its fertilizer.

Key mechanics this relies on (confirmed against the installed
kaggle_environments source, not just the README): FEED and PLACE consume
items from the *acting unit's own inventory*, not the shared shed, so
geese/wheat must be PICKUP'd from the shed before they can be used --
BUILD_COOP/BUILD_PASTURE cost zero gold, only one action.

Harvests are timed to wheat's yield peak (day 4) rather than the first
eligible day (day 2), since HARVEST costs the same one turn either way.
See AGENTS.md / README.md for full rules and CLAUDE.md for the testing
workflow.
"""

WHEAT_SEED_COST = 10
WHEAT_MAX_YIELD_DAY = 4
LAND_COSTS = {"NE": 1000, "SW": 2000, "SE": 4000}
CASH_RESERVE = 50
MAX_SEED_STOCKPILE = 30
# Hire cost is Fibonacci per hand per day (1,1,2,3,5,8,13,21,34,55,89,144...).
# A hand tending ~TILES_PER_ACTOR wheat tiles is worth roughly $90-100/day
# gross, so the 11th hand (cost 144) is the first one that's a net loss --
# cap just below that. Replay analysis showed the old cap of 4 left 40-60
# of 100 owned tiles idle once all land was bought, since actor count never
# scaled past the starting quadrant's needs.
MAX_HANDS = 10
TILES_PER_ACTOR = 5

# Priority order matters: earlier plans get first pick of nearby empty land
# and first claim on spare cash. Cows before geese since milk nets roughly
# double the daily profit per unit of wheat spent feeding it. Cow target
# kept small (2) as a pilot -- an earlier, larger attempt (3+3) regressed,
# though that was confounded by the feed-priority and land-utilization bugs
# fixed since, so it's worth a smaller retry now those are gone.
ANIMAL_PLANS = [
    {"animal": "COW", "structure": "PASTURE", "cost": 400, "target": 2},
    {"animal": "GOOSE", "structure": "COOP", "cost": 300, "target": 4},
]
BUILD_OP = {"PASTURE": "BUILD_PASTURE", "COOP": "BUILD_COOP"}


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
    hire_orders = []  # queued last (see below): least costly thing to truncate

    # ---- Hire hands for the day, sized to owned land ----
    if obs["hour"] == 0:
        desired_hands = min(MAX_HANDS, max(0, len(tiles) // TILES_PER_ACTOR - 1))
        budget = me["money"] - CASH_RESERVE
        n_hire, spent = 0, 0
        while n_hire < desired_hands:
            cost = _fib_hire_cost(n_hire)
            if spent + cost > budget:
                break
            spent += cost
            n_hire += 1
        hire_orders = [["HIRE"]] * n_hire

    # ---- Sell shed surplus, keeping enough wheat to feed the animals ----
    placed_animals = sum(1 for _, _, t in tiles if isinstance(t, dict) and "animal" in t)
    for item, count in shed.items():
        sellable = max(0, count - placed_animals) if item == "WHEAT" else count
        if sellable > 0:
            market.append(["SELL", item, sellable])

    # ---- Decide how much land is wheat vs. reserved for new structures ----
    empty_tiles = [(x, y) for x, y, t in tiles if t is None]
    shed_center = (board_size // 2, board_size // 2)
    empty_by_dist = sorted(empty_tiles, key=lambda t: _manhattan(t[0], t[1], *shed_center))

    build_targets = []  # (x, y, "BUILD_COOP" | "BUILD_PASTURE")
    for plan in ANIMAL_PLANS:
        existing = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == plan["structure"])
        slots_wanted = max(0, plan["target"] - existing)
        claimed, empty_by_dist = empty_by_dist[:slots_wanted], empty_by_dist[slots_wanted:]
        build_targets.extend((x, y, BUILD_OP[plan["structure"]]) for x, y in claimed)
    plant_targets = empty_by_dist

    # ---- Keep enough wheat seed for every tile we intend to plant ----
    seeds_owned = seeds.get("WHEAT", 0)
    target_seeds = min(len(plant_targets), MAX_SEED_STOCKPILE)
    available = me["money"] - CASH_RESERVE
    to_buy = max(0, min(target_seeds - seeds_owned, available // WHEAT_SEED_COST))
    if to_buy > 0:
        market.append(["BUY_SEED", "WHEAT", to_buy])
        available -= to_buy * WHEAT_SEED_COST

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
            placed = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("animal") == animal)
            total = shed.get(animal, 0) + sum(inv.get(animal, 0) for inv in inventories) + placed
            if total < plan["target"] and plan["cost"] <= available:
                market.append(["BUY_ANIMAL", animal, 1])
                available -= plan["cost"]

    # ---- Expand land once comfortably affordable ----
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            break

    # Hiring goes last: maxMarketOrdersPerTurn (10) truncates the market list,
    # and losing a hand-hire for one day is far cheaper than losing a wheat
    # sale or animal purchase. Replay showed a 9-hand hire burst plus a sell
    # and two buys hit 12 orders on one turn -- with hire queued first, the
    # sell and both buys were silently dropped.
    market.extend(hire_orders)

    # ---- Build per-tile task tiers ----
    structure_to_animal = {p["structure"]: p["animal"] for p in ANIMAL_PLANS}
    harvest_targets, feed_targets, water_targets, place_targets, weed_targets = [], [], [], [], []
    for x, y, tile in tiles:
        if isinstance(tile, dict):
            if tile.get("kind") == "PLANT":
                age = obs["day"] - tile["planted_day"]
                if tile["yield_units"] > 0 and age >= WHEAT_MAX_YIELD_DAY:
                    harvest_targets.append((x, y))
                elif not tile["watered_today"]:
                    water_targets.append((x, y))
            elif "animal" in tile:
                if tile["yield_units"] > 0:
                    harvest_targets.append((x, y))
                if not tile["fed_today"]:
                    feed_targets.append((x, y))
            elif tile.get("kind") in structure_to_animal:
                place_targets.append((x, y, structure_to_animal[tile["kind"]]))
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
        n = min(len(unfed_left), shed["WHEAT"])
        assign(
            _shed_access_tiles(board_size),
            lambda ai, t: ["PICKUP", "WHEAT", n],
        )

    # 2. Harvest ripe crops and animal products -- banks cash and frees the
    #    tile for the next cycle.
    assign(harvest_targets, lambda ai, t: ["HARVEST"])

    # 3. Water thirsty wheat -- avoid weeds.
    assign(water_targets, lambda ai, t: ["WATER"])

    # 4. Place a carried animal into its empty structure; if nobody is
    #    carrying one yet, send an idle actor to the shed to pick one up.
    has_animal = lambda ai, t: inventories[ai].get(t[2], 0) > 0
    unplaced_left = assign(place_targets, lambda ai, t: ["PLACE", t[2]], feasible=has_animal)
    for _, _, animal in unplaced_left:
        if shed.get(animal, 0) > 0:
            assign(_shed_access_tiles(board_size), lambda ai, t, animal=animal: ["PICKUP", animal, 1])

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

    # 7. Clear weeds to reclaim the tile for future planting. Low urgency
    #    (no immediate payoff), but left unchecked these compound: replay
    #    analysis showed 31 of 100 owned tiles lost to weeds by day 29 with
    #    no clearing at all.
    assign(weed_targets, lambda ai, t: ["DIG"])

    # 8. Opportunistic bonuses for actors with nothing better to do this
    #    turn: care for a fed animal, or collect its fertilizer.
    for ai in list(unassigned):
        x, y = actor_positions[ai]
        tile = me["tiles"][y][x]
        if isinstance(tile, dict) and "animal" in tile:
            if not tile["cared_today"] and tile["fed_today"]:
                ops[ai] = ["CARE"]
                unassigned.remove(ai)
            elif tile["fertilizer_available"]:
                ops[ai] = ["COLLECT_FERTILIZER"]
                unassigned.remove(ai)

    for ai in unassigned:
        ops[ai] = ["PASS"]

    return {"farmer": ops[0], "hands": ops[1:], "market": market}
