"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Baseline strategy: single-farmer wheat loop (buy seed -> plant -> water ->
harvest -> sell), expanding into BUY_LAND once cash allows. This is meant as
a starting scaffold, not a final strategy -- see AGENTS.md / README.md for
the full rules and CLAUDE.md for the project's testing workflow.
"""

WHEAT_SEED_COST = 10
LAND_COSTS = {"NE": 1000, "SW": 2000, "SE": 4000}


def _tile_at(farm, x, y):
    return farm["tiles"][y][x]


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


def _find_empty_tile(farm):
    for y, row in enumerate(farm["tiles"]):
        for x, tile in enumerate(row):
            if tile is None:
                return x, y
    return None


def agent(obs):
    player = obs["player"]
    me = obs["farms"][player]
    private = obs["private"]
    fx, fy = me["farmer"]
    tile = _tile_at(me, fx, fy)

    market = []

    # Sell anything sitting in the shed.
    for item, count in private["shed"].items():
        if count > 0:
            market.append(["SELL", item, count])

    # Keep a wheat seed in the pipeline.
    have_seed = private["seeds"].get("WHEAT", 0) > 0
    if not have_seed and me["money"] >= WHEAT_SEED_COST:
        market.append(["BUY_SEED", "WHEAT", 1])
        have_seed = True

    # Expand land once we can comfortably afford it.
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            break

    # Standing on our own plant: tend it.
    if isinstance(tile, dict) and tile.get("kind") == "PLANT":
        crop_age = obs["day"] - tile["planted_day"]
        if crop_age >= 2 and tile["yield_units"] > 0:
            return {"farmer": ["HARVEST"], "hands": [], "market": market}
        if not tile["watered_today"]:
            return {"farmer": ["WATER"], "hands": [], "market": market}
        return {"farmer": ["PASS"], "hands": [], "market": market}

    # Standing on empty ground with a seed in hand: plant.
    if tile is None and have_seed:
        return {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": market}

    # Otherwise, walk toward an empty tile to start a new plant.
    target = _find_empty_tile(me)
    if target is not None:
        move = _step_toward(fx, fy, target[0], target[1])
        if move:
            return {"farmer": [move], "hands": [], "market": market}

    return {"farmer": ["PASS"], "hands": [], "market": market}
