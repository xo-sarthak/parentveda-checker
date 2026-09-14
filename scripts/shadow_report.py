"""How does the shadow judge compare with the real one, across every queued
article so far?  Read-only; no model calls.

    python scripts/shadow_report.py [--since 2026-09-01] [--verbose]

Answers the question the shadow exists for: could the cheap model replace
the expensive one? Three things matter, in order — did it flag the same
headline issue, did it miss anything the real judge marked Must Fix, and
how far apart are the scores.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import store  # noqa: E402

_STOP = set("a an the of to in on for and or is are be with without not no this that "
            "it its as at by from into than then too very".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if w not in _STOP and len(w) > 2}


def _similar(a: str, b: str) -> bool:
    """Loose match between two finding summaries: share a third of their words."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.34


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select s.version_id, s.model as shadow_model, s.overall as s_overall, "
            "s.verdict as s_verdict, s.review as s_review, s.cost_usd as s_cost, s.error, "
            "r.id as run_id, r.model, r.overall, r.verdict, r.cost_usd, a.title "
            "from shadow_reviews s "
            "join versions v on v.id = s.version_id "
            "join articles a on a.id = v.article_id "
            "left join lateral (select * from runs where version_id = s.version_id "
            "  and kind='review' order by created_at desc limit 1) r on true "
            "where (%s::date is null or s.created_at >= %s::date) "
            "order by s.created_at", (args.since, args.since))
        rows = cur.fetchall()
        findings = {}
        for row in rows:
            if row["run_id"]:
                cur.execute("select tier, summary, headline, parameter from feedback "
                            "where run_id=%s order by position", (row["run_id"],))
                findings[str(row["run_id"])] = cur.fetchall()

    if not rows:
        print("No shadow reviews yet.")
        return

    n = len(rows)
    errors = [r for r in rows if r["error"]]
    paired = [r for r in rows if r["run_id"] and not r["error"]]
    print(f"{n} shadow reviews ({rows[0]['shadow_model']}), {len(paired)} paired with a real run, "
          f"{len(errors)} shadow errors")
    if not paired:
        return

    diffs = [float(r["s_overall"]) - float(r["overall"]) for r in paired]
    print(f"\nScore gap (shadow − real): mean {sum(diffs)/len(diffs):+.2f}, "
          f"max {max(diffs, key=abs):+.2f}, within ±0.2 on {sum(abs(d) <= 0.2 for d in diffs)}/{len(diffs)}")

    verdict_agree = sum(r["s_verdict"] == r["verdict"] for r in paired)
    print(f"Verdict agrees: {verdict_agree}/{len(paired)}")

    headline_hits = 0
    must_missed = Counter()
    must_total = 0
    details = []
    for r in paired:
        real = findings.get(str(r["run_id"]), [])
        shadow = (r["s_review"] or {}).get("feedback", [])
        real_head = next((f for f in real if f["headline"]), None)
        s_head = next((f for f in shadow if f.get("headline")), None)
        head_hit = bool(real_head and any(_similar(real_head["summary"], f["summary"]) for f in shadow))
        headline_hits += head_hit
        missed = []
        for f in real:
            if f["tier"] != "must":
                continue
            must_total += 1
            if not any(_similar(f["summary"], g["summary"]) for g in shadow):
                must_missed[f["parameter"]] += 1
                missed.append(f["summary"])
        details.append((r, real_head, s_head, head_hit, missed))

    print(f"Shadow found the real headline issue: {headline_hits}/{len(paired)}")
    print(f"Real Must Fix items the shadow missed: {sum(must_missed.values())}/{must_total}")
    if must_missed:
        print("  by parameter:", ", ".join(f"{k} ×{v}" for k, v in must_missed.most_common()))

    cost_real = sum(float(r["cost_usd"] or 0) for r in paired)
    cost_shadow = sum(float(r["s_cost"] or 0) for r in paired)
    print(f"\nCost over these {len(paired)}: real ${cost_real:.2f}, shadow ${cost_shadow:.2f} "
          f"(₹{cost_real*store.INR_PER_USD:,.0f} vs ₹{cost_shadow*store.INR_PER_USD:,.0f})")

    if args.verbose:
        print("\n— per article —")
        for r, real_head, s_head, hit, missed in details:
            print(f"\n{r['title'][:70]}")
            print(f"  real   {float(r['overall']):.2f} {r['verdict']:<16} headline: {real_head['summary'] if real_head else '-'}")
            print(f"  shadow {float(r['s_overall']):.2f} {r['s_verdict']:<16} headline: {s_head['summary'] if s_head else '-'}  {'✓' if hit else '✗ headline missed'}")
            for m in missed:
                print(f"    missed must-fix: {m}")

    print("\nRule of thumb: switch only when the headline is found on 9 in 10 and no "
          "medical/safety Must Fix is missed. Each miss is a candidate general rule.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
