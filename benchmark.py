"""Side-by-side proof: always-strong with the raw prompt vs. the router with cleaned prompts.

    python benchmark.py                 # uses bench_set.json
    python benchmark.py my_set.json

Quality is a crude automatic check: every string in "expect" must appear in the
answer. Items with an empty "expect" are not scored. In demo (mock) mode no
quality is reported, because placeholder answers prove nothing about quality.
"""

import json
import sys
from pathlib import Path

from router import RouterError, execute_plan, load_config, make_plan, run_baseline


def check(answer, expect):
    if not expect:
        return None
    low = answer.lower()
    return all(e.lower() in low for e in expect)


def label(v):
    return "n/a" if v is None else ("pass" if v else "FAIL")


def run(cfg, items):
    rows = []
    for it in items:
        plan = make_plan(it["prompt"], cfg)
        routed = execute_plan(plan, cfg)
        base = run_baseline(it["prompt"], cfg, sum(p["est_out"] for p in plan["parts"]))
        mocked = routed["mock"] or base["mock"]
        expect = it.get("expect", [])
        rows.append({
            "prompt": it["prompt"],
            "tiers": "+".join(p["tier"] for p in plan["parts"]),
            "baseline_cost": base["cost"], "router_cost": routed["cost"],
            "baseline_ok": None if mocked else check(base["answer"], expect),
            "router_ok": None if mocked else check(routed["answer"], expect),
            "mock": mocked,
        })
    return rows


def summarize(rows):
    base = sum(r["baseline_cost"] for r in rows)
    routed = sum(r["router_cost"] for r in rows)
    saving = (base - routed) / base * 100 if base else 0.0
    scored = [r for r in rows if r["baseline_ok"] is not None]
    return {
        "baseline_cost": base, "router_cost": routed, "saving_percent": saving,
        "scored": len(scored),
        "baseline_pass": sum(bool(r["baseline_ok"]) for r in scored),
        "router_pass": sum(bool(r["router_ok"]) for r in scored),
        "mock": any(r["mock"] for r in rows),
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else Path(__file__).with_name("bench_set.json")
    try:
        cfg = load_config()
        items = json.loads(path.read_text(encoding="utf-8"))
        rows = run(cfg, items)
    except (RouterError, OSError, json.JSONDecodeError) as e:
        print(f"Error: {e}")
        return 1

    print(f"{'#':>2}  {'request':<34} {'routed to':<20} {'strong-only':>12} {'router':>12}  quality (strong / router)")
    for i, r in enumerate(rows, 1):
        short = (r["prompt"].replace("\n", " ")[:31] + "...") if len(r["prompt"]) > 34 else r["prompt"].replace("\n", " ")
        print(f"{i:>2}  {short:<34} {r['tiers']:<20} ${r['baseline_cost']:>11.6f} ${r['router_cost']:>11.6f}  "
              f"{label(r['baseline_ok'])} / {label(r['router_ok'])}")
    s = summarize(rows)
    print(f"\nTotal cost, always strong + raw prompt: ${s['baseline_cost']:.6f}")
    print(f"Total cost, router + cleaned prompt:     ${s['router_cost']:.6f}")
    print(f"Cost saved: {s['saving_percent']:.1f}%")
    if s["scored"]:
        print(f"Quality on {s['scored']} checkable requests: strong-only {s['baseline_pass']}/{s['scored']}, "
              f"router {s['router_pass']}/{s['scored']}")
    if s["mock"]:
        print("\nDEMO MODE: costs use estimated tokens and placeholder prices, and quality is not measured. "
              "Turn on real models in models.json to get a real comparison.")
    Path("benchmark_results.json").write_text(json.dumps({"summary": s, "rows": rows}, indent=2), encoding="utf-8")
    print("Saved benchmark_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
