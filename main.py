"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Strategy: multi-tile wheat farming worked by the farmer plus a batch of
hired hands. Every unlocked tile is a farming target; each turn, every
active unit (farmer + hands) is greedily matched to the nearest tile that
needs attention, prioritizing harvest > water > expand so nothing decays
or turns to a weed while a unit is busy elsewhere. Hands are re-hired at
the start of each day (HIRE cost resets daily and follows a Fibonacci
sequence), sized to the amount of owned land so a small starting quadrant
doesn't over-hire. Harvests are timed to wheat's yield peak (day 4) rather
than the first eligible day (day 2), since HARVEST costs the same one turn
either way and banking more yield per action is a better use of turns.
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


def agent(obs):
    me = obs["farms"][obs["player"]]
    private = obs["private"]
    tiles = _own_tiles(me)

    actor_positions = [tuple(me["farmer"])] + [tuple(h) for h in me["hands"]]
    ops = [None] * len(actor_positions)

    market = []

    # Sell anything sitting in the shed.
    for item, count in private["shed"].items():
        if count > 0:
            market.append(["SELL", item, count])

    # Hire hands for the day, sized to owned land, at the start of the day.
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

    # Keep enough wheat seed on hand to plant every currently-empty tile,
    # bought in bulk (seeds have no stock cap) so expansion isn't gated on a
    # one-seed-per-day trickle. Never spend below the cash reserve.
    empty_tile_count = sum(1 for _, _, t in tiles if t is None)
    seeds_owned = private["seeds"].get("WHEAT", 0)
    target_seeds = min(empty_tile_count, MAX_SEED_STOCKPILE)
    affordable = max(0, (me["money"] - CASH_RESERVE) // WHEAT_SEED_COST)
    to_buy = max(0, min(target_seeds - seeds_owned, affordable))
    if to_buy > 0:
        market.append(["BUY_SEED", "WHEAT", to_buy])

    # Expand land once we can comfortably afford it.
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            break

    # Priority tiers of tile targets: ripe crops, thirsty crops, empty land.
    harvest_targets, water_targets, plant_targets = [], [], []
    for x, y, tile in tiles:
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            age = obs["day"] - tile["planted_day"]
            if tile["yield_units"] > 0 and age >= WHEAT_MAX_YIELD_DAY:
                harvest_targets.append((x, y))
            elif not tile["watered_today"]:
                water_targets.append((x, y))
        elif tile is None:
            plant_targets.append((x, y))

    unassigned = list(range(len(actor_positions)))
    plant_budget = seeds_owned  # simultaneous PLANTs this turn can't exceed owned seeds

    for kind, targets in (("harvest", harvest_targets), ("water", water_targets), ("plant", plant_targets)):
        remaining_targets = list(targets)

        # Units already standing on a target act immediately, no move needed.
        for ai in list(unassigned):
            pos = actor_positions[ai]
            if pos in remaining_targets:
                if kind == "plant" and plant_budget <= 0:
                    continue
                ops[ai] = {"harvest": ["HARVEST"], "water": ["WATER"], "plant": ["PLANT", "WHEAT"]}[kind]
                if kind == "plant":
                    plant_budget -= 1
                unassigned.remove(ai)
                remaining_targets.remove(pos)

        # Remaining units head for the nearest remaining target, greedily.
        while unassigned and remaining_targets:
            best = None
            for ai in unassigned:
                ax, ay = actor_positions[ai]
                for t in remaining_targets:
                    d = _manhattan(ax, ay, t[0], t[1])
                    if best is None or d < best[0]:
                        best = (d, ai, t)
            _, ai, t = best
            move = _step_toward(actor_positions[ai][0], actor_positions[ai][1], t[0], t[1])
            ops[ai] = [move] if move else ["PASS"]
            unassigned.remove(ai)
            remaining_targets.remove(t)

    for ai in unassigned:
        ops[ai] = ["PASS"]

    return {"farmer": ops[0], "hands": ops[1:], "market": market}
