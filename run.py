"""Route one request.

    python run.py "Hi! Could you please tell me the capital of France? Thanks!"
    python run.py "1. What is the capital of Japan? 2. Explain why the sky is blue step by step."
    python run.py "your request" --force strong      # override the router
    python run.py "your request" --yes               # no questions asked
"""

import argparse
import sys

from router import (RouterError, TIERS, baseline_estimate, est_tokens, execute_plan,
                    load_config, log_decision, make_plan)

CHOICES = {"l": "local", "local": "local", "m": "mid", "mid": "mid", "s": "strong", "strong": "strong"}


def money(x):
    return f"${x:.6f}"


def show_plan(plan, baseline_est):
    print("\n=== ROUTING PLAN ===")
    for i, p in enumerate(plan["parts"], 1):
        saved = est_tokens(p["original"]) - est_tokens(p["cleaned"])
        print(f"\nPart {i} of {len(plan['parts'])}  ->  {p['tier'].upper()}")
        print(f"  original:   {p['original']!r}")
        print(f"  cleaned:    {p['cleaned']!r}  (about {max(saved, 0)} input tokens saved)")
        print(f"  difficulty: {p['score']}/10  ({'; '.join(p['reasons'])})")
        print("  est. cost:  " + " | ".join(f"{t} {money(p['est_cost'][t])}" for t in TIERS))
        print(f"  why:        {p['why']}")
    routed = sum(p["est_cost"][p["tier"]] for p in plan["parts"])
    print(f"\nEstimated cost with router:               {money(routed)}")
    print(f"Estimated cost, always strong + raw prompt: {money(baseline_est)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Route a request to local, mid or strong model.")
    ap.add_argument("request")
    ap.add_argument("--force", choices=TIERS, help="send every part to this tier")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--log", default="routing_log.jsonl")
    args = ap.parse_args(argv)

    try:
        cfg = load_config()
        plan = make_plan(args.request, cfg, force=args.force)
        base = baseline_estimate(plan, cfg)
        show_plan(plan, base)

        if not args.yes and sys.stdin.isatty():
            ans = input("\nPress Enter to run, l/m/s to force local/mid/strong, q to quit: ").strip().lower()
            if ans == "q":
                return 0
            if ans in CHOICES:
                plan = make_plan(args.request, cfg, force=CHOICES[ans])
                base = baseline_estimate(plan, cfg)
                show_plan(plan, base)

        result = execute_plan(plan, cfg)
    except RouterError as e:
        print(f"Error: {e}")
        return 1

    print("\n=== ANSWER ===")
    print(result["answer"])
    print(f"\nActual cost: {money(result['cost'])}")
    if result["mock"]:
        print("(Demo mode: answers are placeholders and token counts are estimates. "
              "Set \"live\": true for a tier in models.json to call a real model.)")
    log_decision(args.log, plan, result, base)
    print(f"Decision logged to {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
