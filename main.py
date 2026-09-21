"""
Kaggriculture submission entrypoint. Must expose `agent(obs)` returning
{"farmer": [op, ...], "hands": [[op, ...], ...], "market": [[op, ...], ...]}.

Strategy: multi-tile wheat farming with a single farmer. Every unlocked tile
in owned quadrants is a farming target, not just the one the farmer starts
on -- the farmer cycles between watering existing plants, harvesting ripe
ones, and expanding into empty tiles, always picking the nearest tile that
needs attention. Harvests are timed to wheat's yield peak (day 4) rather
than the first eligible day (day 2), since HARVEST costs the same one turn
either way and banking more yield per action is a better use of turns.
See AGENTS.md / README.md for full rules and CLAUDE.md for the testing
workflow.
"""

WHEAT_SEED_COST = 10
WHEAT_FIRST_YIELD_DAY = 2
WHEAT_MAX_YIELD_DAY = 4
LAND_COSTS = {"NE": 1000, "SW": 2000, "SE": 4000}
CASH_RESERVE = 50
MAX_SEED_STOCKPILE = 10


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


def _nearest(fx, fy, coords):
    return min(coords, key=lambda c: _manhattan(fx, fy, c[0], c[1]))


def agent(obs):
    player = obs["player"]
    me = obs["farms"][player]
    private = obs["private"]
    fx, fy = me["farmer"]
    tiles = _own_tiles(me)
    tile_here = me["tiles"][fy][fx]

    market = []

    # Sell anything sitting in the shed.
    for item, count in private["shed"].items():
        if count > 0:
            market.append(["SELL", item, count])

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
    have_seed = seeds_owned > 0

    # Expand land once we can comfortably afford it.
    for quadrant, cost in LAND_COSTS.items():
        if quadrant not in me["unlocked_quadrants"] and me["money"] >= cost * 2:
            market.append(["BUY_LAND"])
            break

    # Standing on a tile that needs attention right now: act without moving.
    if isinstance(tile_here, dict) and tile_here.get("kind") == "PLANT":
        age = obs["day"] - tile_here["planted_day"]
        if tile_here["yield_units"] > 0 and age >= WHEAT_MAX_YIELD_DAY:
            return {"farmer": ["HARVEST"], "hands": [], "market": market}
        if not tile_here["watered_today"]:
            return {"farmer": ["WATER"], "hands": [], "market": market}
    elif tile_here is None and have_seed:
        return {"farmer": ["PLANT", "WHEAT"], "hands": [], "market": market}

    # Otherwise, head for the nearest tile that needs attention: harvest
    # ripe plants first, then water growing ones (avoid weeds), then expand
    # into empty tiles.
    harvest_targets, water_targets, plant_targets = [], [], []
    for x, y, tile in tiles:
        if isinstance(tile, dict) and tile.get("kind") == "PLANT":
            age = obs["day"] - tile["planted_day"]
            if tile["yield_units"] > 0 and age >= WHEAT_MAX_YIELD_DAY:
                harvest_targets.append((x, y))
            elif not tile["watered_today"]:
                water_targets.append((x, y))
        elif tile is None and have_seed:
            plant_targets.append((x, y))

    for targets in (harvest_targets, water_targets, plant_targets):
        if targets:
            tx, ty = _nearest(fx, fy, targets)
            move = _step_toward(fx, fy, tx, ty)
            if move:
                return {"farmer": [move], "hands": [], "market": market}
            break

    return {"farmer": ["PASS"], "hands": [], "market": market}
