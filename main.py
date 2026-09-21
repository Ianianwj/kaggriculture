"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Strategy: a multi-tile, multi-actor wheat farm (farmer + hired hands) that
also runs a small goose operation fed from its own wheat surplus. Every
turn, every active unit is greedily matched to the nearest task in this
priority order: harvest (crops and animal products) > feed unfed geese >
water thirsty wheat > place purchased geese into empty coops > build new
coops > plant wheat on the rest of the land. Actors that end up idle on a
useful tile opportunistically CARE for a fed goose or collect fertilizer.

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
MAX_HANDS = 4
TILES_PER_ACTOR = 5
GOOSE_COST = 300
GOOSE_TARGET = 4


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
        market.extend([["HIRE"]] * n_hire)

    # ---- Sell shed surplus, keeping enough wheat to feed the geese ----
    placed_geese = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("animal") == "GOOSE")
    wheat_reserve = placed_geese
    for item, count in shed.items():
        sellable = max(0, count - wheat_reserve) if item == "WHEAT" else count
        if sellable > 0:
            market.append(["SELL", item, sellable])

    # ---- Decide how much land is wheat vs. reserved for new coops ----
    empty_tiles = [(x, y) for x, y, t in tiles if t is None]
    existing_coops = sum(1 for _, _, t in tiles if isinstance(t, dict) and t.get("kind") == "COOP")
    coop_slots_wanted = max(0, GOOSE_TARGET - existing_coops)
    shed_center = (board_size // 2, board_size // 2)
    empty_by_dist = sorted(empty_tiles, key=lambda t: _manhattan(t[0], t[1], *shed_center))
    coop_build_targets = empty_by_dist[:coop_slots_wanted]
    plant_targets = empty_by_dist[coop_slots_wanted:]

    # ---- Keep enough wheat seed for every tile we intend to plant ----
    seeds_owned = seeds.get("WHEAT", 0)
    target_seeds = min(len(plant_targets), MAX_SEED_STOCKPILE)
    affordable = max(0, (me["money"] - CASH_RESERVE) // WHEAT_SEED_COST)
    to_buy = max(0, min(target_seeds - seeds_owned, affordable))
    if to_buy > 0:
        market.append(["BUY_SEED", "WHEAT", to_buy])

    # ---- Buy geese up to target ----
    geese_total = (
        shed.get("GOOSE", 0)
        + sum(inv.get("GOOSE", 0) for inv in inventories)
        + placed_geese
    )
    if geese_total < GOOSE_TARGET and me["money"] - CASH_RESERVE >= GOOSE_COST:
        market.append(["BUY_ANIMAL", "GOOSE", 1])

    # ---- Expand land once comfortably affordable ----
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            break

    # ---- Build per-tile task tiers ----
    harvest_targets, feed_targets, water_targets, place_targets = [], [], [], []
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
            elif tile.get("kind") == "COOP":
                place_targets.append((x, y))

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

    # 1. Harvest ripe crops and animal products -- highest priority, banks
    #    cash and frees the tile for the next cycle.
    assign(harvest_targets, lambda ai, t: ["HARVEST"])

    # 2. Feed unfed geese. Only actors already carrying wheat can act (from
    #    a recent harvest or an earlier pickup); leftover unfed geese get an
    #    idle actor sent to fetch wheat from the shed for a future turn.
    has_wheat = lambda ai, t: inventories[ai].get("WHEAT", 0) > 0
    unfed_left = assign(feed_targets, lambda ai, t: ["FEED"], feasible=has_wheat)
    if unfed_left and shed.get("WHEAT", 0) > 0:
        n = min(len(unfed_left), shed["WHEAT"])
        assign(
            _shed_access_tiles(board_size),
            lambda ai, t: ["PICKUP", "WHEAT", n],
        )

    # 3. Water thirsty wheat -- avoid weeds.
    assign(water_targets, lambda ai, t: ["WATER"])

    # 4. Place a carried goose into an empty coop; if nobody is carrying one
    #    yet, send an idle actor to the shed to pick one up.
    has_goose = lambda ai, t: inventories[ai].get("GOOSE", 0) > 0
    unplaced_left = assign(place_targets, lambda ai, t: ["PLACE", "GOOSE"], feasible=has_goose)
    if unplaced_left and shed.get("GOOSE", 0) > 0:
        assign(_shed_access_tiles(board_size), lambda ai, t: ["PICKUP", "GOOSE", 1])

    # 5. Build new coops on reserved land.
    assign(coop_build_targets, lambda ai, t: ["BUILD_COOP"])

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

    # 7. Opportunistic bonuses for actors with nothing better to do this
    #    turn: care for a fed goose, or collect its fertilizer.
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
