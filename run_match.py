"""
Run main.py against a baseline opponent and print the result.

Usage:
    python run_match.py [opponent] [--steps N] [--replay path.json]

opponent: "random" (default), "pass", or "starter"
"""

import argparse
import json

from kaggle_environments import make


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("opponent", nargs="?", default="random")
    parser.add_argument("--steps", type=int, default=720)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--replay", default=None)
    args = parser.parse_args()

    wins = losses = ties = 0
    total_reward = 0.0

    for trial in range(args.trials):
        env = make("kaggriculture", configuration={"episodeSteps": args.steps}, debug=(args.trials == 1))
        env.run(["main.py", args.opponent])

        final = env.steps[-1]
        my_reward, opp_reward = final[0].reward, final[1].reward
        total_reward += my_reward
        if my_reward > opp_reward:
            wins += 1
        elif my_reward < opp_reward:
            losses += 1
        else:
            ties += 1

        if args.trials == 1:
            for i, s in enumerate(final):
                print(f"Player {i}: reward={s.reward}, status={s.status}")

        if args.replay:
            path = args.replay if args.trials == 1 else args.replay.replace(".json", f"_{trial}.json")
            with open(path, "w") as f:
                json.dump(env.toJSON(), f)
            print(f"Replay written to {path}")

    if args.trials > 1:
        print(f"vs {args.opponent}: {wins}W {losses}L {ties}T over {args.trials} trials, "
              f"avg reward={total_reward / args.trials:.1f}")


if __name__ == "__main__":
    main()
