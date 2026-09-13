"""Apply a saved review's findings to the cradle cap article with a chosen
rewriter. Line findings are swapped in by code; only structural ones (and any
line finding whose quote no longer matches) go to the model.

    python scripts/test_rewrite.py <review-model> <rewrite-model> [skip-indexes]
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, editor, parse, store

VERSION = "8cab998a-9011-4edc-aa90-e7dd1f2f09a8"


def main(review_model, rewrite_model, skip=""):
    skip = {int(i) for i in skip.split(",") if i}
    r = json.loads(Path(f"data/calibration/{review_model}-cradle.json").read_text(encoding="utf-8"))
    with store.connect() as c:
        body = c.execute("select body from versions where id=%s", (VERSION,)).fetchone()["body"]
    body, _ = parse.split_internal(body)

    accepted = [dict(f, kind=f["type"], position=i)
                for i, f in enumerate(r["feedback"]) if i not in skip]
    swapped, to_model = [], []
    for f in accepted:
        q = f.get("quote")
        if f["kind"] == "line" and q and body.count(q) == 1:
            body = body.replace(q, f["proposed"])
            swapped.append(f["summary"])
        else:
            to_model.append(f)
    print(f"swapped in code ({len(swapped)}):"); [print("   -", s) for s in swapped]
    print(f"to model ({len(to_model)}):"); [print("   -", f['kind'], "|", f['summary']) for f in to_model]

    t0 = time.monotonic()
    out = editor.rewrite(body, to_model, model=rewrite_model, effort=config.EFFORT)
    secs = round(time.monotonic() - t0)
    out["_cost_usd"] = store.cost_usd(out["_usage"]); out["_seconds"] = secs
    out["_swapped"] = swapped; out["_before"] = body
    Path(f"data/calibration/{review_model}--{rewrite_model}-rewrite.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    d = out["diff"]
    print(f"\n{rewrite_model}: {secs}s  ${out['_cost_usd']:.4f}  {out['_usage']}")
    print(f"words {d['words_before']} -> {d['words_after']}  "
          f"(+{d['added']} para, -{d['removed']}, ~{d['edited']} edited)")
    for ch in d["changes"]:
        print(f"\n[{ch['kind'].upper()}]")
        if ch["before"]: print("  BEFORE:", ch["before"][:600])
        if ch["after"]:  print("  AFTER: ", ch["after"][:900])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(*sys.argv[1:])
