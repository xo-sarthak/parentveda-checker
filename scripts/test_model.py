"""Score one stored article version with one model; save the review to
data/calibration/. Nothing is written to the shared history.

    python scripts/test_model.py <model> [version_id]
"""
import json, sys, time
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from pathlib import Path

from app import config, content, judge, parse, store

DEFAULT_VERSION = "8cab998a-9011-4edc-aa90-e7dd1f2f09a8"  # cradle cap, Opus 8.63


def main(model: str, version_id: str = DEFAULT_VERSION):
    out = Path("data/calibration") / f"{model}-cradle.json"
    if out.exists():                      # already paid for: just show it
        r = json.loads(out.read_text(encoding="utf-8"))
    else:
        with store.connect() as c:
            body = c.execute("select body from versions where id=%s", (version_id,)).fetchone()["body"]
        body, _ = parse.split_internal(body)
        t0 = time.monotonic()
        r = judge.review(body, content.catalogue(), model=model, effort=config.EFFORT)
        r["_seconds"] = round(time.monotonic() - t0)
        r["_cost_usd"] = store.cost_usd(r["_usage"])
        out.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")

    u = r["_usage"]
    print(f"{model}: overall {r['overall']}  verdict {r['verdict']}  "
          f"{r['_seconds']}s  ${r['_cost_usd']:.3f}  "
          f"in {u['in']} out {u['out']} reasoning {u.get('reasoning', '-')}")
    print("scope:", r["article_level"]["scope"])
    print("job:  ", r["article_level"]["job"])
    print("leave:", r["article_level"]["leave_alone"])
    print("length:", r["article_level"]["length_note"])
    for p, s in r["scores"].items():
        print(f"  {s['score']:>4}  {p:<22} {s['justification']}")
    if r["blockers"]:
        print("blockers:", r["blockers"])
    print(f"findings ({len(r['feedback'])}):")
    for f in r["feedback"]:
        tag = "HEADLINE " if f.get("headline") else ""
        val = " [validate]" if f.get("needs_validation") else ""
        print(f"  - {tag}{f['tier']}/{f['type']} · {f['parameter']}{val}\n    {f['summary']}")
        if f.get("quote"):
            print(f"    quote: {f['quote'][:140]!r}")
        if f.get("proposed"):
            print(f"    proposed: {f['proposed'][:220]!r}")
        print(f"    why: {f['rationale'][:220]}")
    print("expert:", r["expert_review"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:])
