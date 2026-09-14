"""Persistence. Every run is reproducible: model, effort and ruleset version pinned."""
import hashlib
import json
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app import config, content

PRICING = {  # $/1M: in, out, cache_write, cache_read
    "claude-opus-5": (5, 25, 6.25, 0.5),
    "claude-sonnet-5": (2, 10, 2.5, 0.2),
    "claude-haiku-4-5": (1, 5, 1.25, 0.1),
    # OpenAI has no cache-write premium; cached input is simply cheaper.
    # Reasoning tokens are billed as output and already inside `out`.
    "gpt-5.6-sol": (4, 20, 0, 0.4),
    "gpt-5.6-terra": (2, 12, 0, 0.2),
    "gpt-5.6-luna": (0.2, 1.2, 0, 0.02),
    "gpt-5": (1.25, 10, 0, 0.125),
    "gpt-5-mini": (0.25, 2, 0, 0.025),
    "gpt-4.1": (2, 8, 0, 0.5),
    "o3": (2, 8, 0, 0.5),
}


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30,
                           row_factory=dict_row)


def cost_usd(usage: dict) -> float:
    p = PRICING.get(usage["model"])
    if not p:
        return 0.0
    usd = (usage["in"] * p[0] / 1e6 + usage["out"] * p[1] / 1e6
           + usage.get("cache_write", 0) * p[2] / 1e6
           + usage.get("cache_read", 0) * p[3] / 1e6)
    if usage.get("batch"):
        usd /= 2  # Batch API: half price on every token
    return round(usd, 4)


INR_PER_USD = float(os.environ.get("PV_INR_RATE", "95"))

# Typical token shapes for each job, measured on 1,300–2,600-word articles.
# Only used to tell the intern roughly what a button will cost before they
# press it; the real figure is recorded from usage afterwards.
_SHAPES = {"rewrite": (7_000, 3_500), "rescore": (11_000, 10_000),
           "sheet": (6_000, 2_600), "images": (2_600, 700)}


def estimate_usd(model: str, job: str) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    tin, tout = _SHAPES[job]
    return round(tin * p[0] / 1e6 + tout * p[1] / 1e6, 4)


def estimates(models: dict) -> dict:
    """What each paid step would cost with this engine's models, in $ and ₹."""
    out = {"inr_rate": INR_PER_USD}
    for job, key in (("rewrite", "edit"), ("rescore", "score"),
                     ("sheet", "doctor"), ("images", "images")):
        usd = estimate_usd(models[key], job)
        out[job] = {"usd": usd, "inr": round(usd * INR_PER_USD, 2), "model": models[key]}
    return out


def ruleset_version() -> str:
    text = content.get("ruleset")
    return "v1-" + hashlib.sha256(text.encode()).hexdigest()[:8]


def save_review(title: str, body: str, review: dict, *, author: str | None = None,
                article_id: str | None = None, kind: str = "review",
                source: str = "upload", actor: str | None = None,
                duration_s: float | None = None) -> dict[str, Any]:
    """Persist an article version and its review. Returns the ids created."""
    u = review["_usage"]
    with connect() as conn, conn.cursor() as cur:
        if article_id is None:
            cur.execute(
                "insert into articles (title, article_type, author, status, created_by) "
                "values (%s,%s,%s,'reviewed',%s) returning id",
                (title, review.get("article_type"), author, actor))
            article_id = cur.fetchone()["id"]
        else:
            cur.execute("update articles set status='reviewed', updated_at=now() "
                        "where id=%s", (article_id,))

        cur.execute("select coalesce(max(version_no),0)+1 as n from versions "
                    "where article_id=%s", (article_id,))
        vno = cur.fetchone()["n"]
        cur.execute(
            "insert into versions (article_id, version_no, body, word_count, source) "
            "values (%s,%s,%s,%s,%s) returning id",
            (article_id, vno, body, len(body.split()), source))
        version_id = cur.fetchone()["id"]

        run_id = _insert_run(cur, version_id, review, kind=kind, actor=actor,
                             duration_s=duration_s)
        conn.commit()
    return {"article_id": article_id, "version_id": version_id,
            "run_id": run_id, "version_no": vno}


def _insert_run(cur, version_id, review: dict, *, kind: str, actor: str | None,
                duration_s: float | None):
    """The run, its twelve scores and its findings, each with an open decision."""
    u = review["_usage"]
    cur.execute(
        "insert into runs (version_id, kind, model, effort, ruleset_version, "
        "overall, verdict, blockers, article_level, expert_review, "
        "tokens_in, tokens_out, cache_write, cache_read, cost_usd, actor_email, "
        "duration_s, batch) "
        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
        (version_id, kind, u["model"], u.get("effort", "high"), ruleset_version(),
         review["overall"], review["verdict"], json.dumps(review["blockers"]),
         json.dumps(review["article_level"]), json.dumps(review["expert_review"]),
         u["in"], u["out"], u.get("cache_write", 0), u.get("cache_read", 0),
         cost_usd(u), actor, duration_s, bool(u.get("batch"))))
    run_id = cur.fetchone()["id"]

    for param, v in review["scores"].items():
        cur.execute("insert into scores (run_id, parameter, score, justification) "
                    "values (%s,%s,%s,%s)",
                    (run_id, param, v["score"], v["justification"]))

    rank = {"must": 0, "should": 1, "polish": 2}
    items = sorted(review["feedback"], key=lambda f: rank[f["tier"]])
    for i, f in enumerate(items):
        cur.execute(
            "insert into feedback (run_id, tier, kind, parameter, summary, "
            "quote, proposed, rationale, position, headline, needs_validation) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (run_id, f["tier"], f["type"], f["parameter"], f["summary"],
             f.get("quote"), f["proposed"], f.get("rationale"), i,
             bool(f.get("headline")), bool(f.get("needs_validation"))))
        cur.execute("insert into decisions (feedback_id) values (%s)",
                    (cur.fetchone()["id"],))
    return run_id


def attach_run(version_id: str, review: dict, *, actor: str | None,
               duration_s: float | None, kind: str = "verify") -> dict[str, Any]:
    """A run on a version that already exists — a re-score, or a batched
    review landing on the draft that was queued."""
    with connect() as conn, conn.cursor() as cur:
        run_id = _insert_run(cur, version_id, review, kind=kind, actor=actor,
                             duration_s=duration_s)
        if kind == "review":
            cur.execute("update articles set status='reviewed', article_type=%s, "
                        "updated_at=now() where id=(select article_id from versions "
                        "where id=%s)", (review.get("article_type"), version_id))
        conn.commit()
    return {"version_id": version_id, "run_id": run_id}


def save_draft(title: str, body: str, *, author: str | None, actor: str | None) -> dict[str, Any]:
    """An article and its first version with no review yet — what goes into
    the queue. The review arrives later as a run on this version."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into articles (title, author, status, created_by) "
            "values (%s,%s,'draft',%s) returning id", (title, author, actor))
        article_id = cur.fetchone()["id"]
        cur.execute(
            "insert into versions (article_id, version_no, body, word_count, source, "
            "created_by) values (%s,1,%s,%s,'upload',%s) returning id",
            (article_id, body, len(body.split()), actor))
        version_id = cur.fetchone()["id"]
        conn.commit()
    return {"article_id": article_id, "version_id": version_id, "version_no": 1}


def save_version(article_id: str, body: str, *, source: str, actor: str | None,
                 note: str | None = None) -> dict[str, Any]:
    """A new version of the article with no review attached — what Apply
    produces now that a rewrite is not automatically re-scored."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(version_no),0)+1 as n from versions "
                    "where article_id=%s", (article_id,))
        vno = cur.fetchone()["n"]
        cur.execute(
            "insert into versions (article_id, version_no, body, word_count, source, "
            "note, created_by) values (%s,%s,%s,%s,%s,%s,%s) returning id",
            (article_id, vno, body, len(body.split()), source, note, actor))
        version_id = cur.fetchone()["id"]
        cur.execute("update articles set updated_at=now() where id=%s",
                    (article_id,))
        conn.commit()
    return {"article_id": article_id, "version_id": version_id, "version_no": vno}


def decide(feedback_id: str, outcome: str, *, by: str | None = None,
           edited: str | None = None, auto: bool = False) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update decisions set outcome=%s, decided_by=%s, edited_text=%s, "
            "auto=%s, decided_at=now() where feedback_id=%s",
            (outcome, by, edited, auto, feedback_id))
        conn.commit()


def accepted_feedback(run_id: str) -> list[dict]:
    """What the Editor will act on."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select f.* , d.edited_text from feedback f join decisions d "
            "on d.feedback_id = f.id where f.run_id=%s and d.outcome in "
            "('accepted','edited') order by f.position", (run_id,))
        return cur.fetchall()


def search(q: str, limit: int = 25) -> list[dict]:
    """Find past work by title or topic — 'that article from last month'."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select a.id, a.title, a.status, a.created_at, "
            "  (select overall from runs r join versions v on v.id=r.version_id "
            "   where v.article_id=a.id order by r.created_at desc limit 1) as latest_score "
            "from articles a "
            "where to_tsvector('english', coalesce(a.title,'')||' '||coalesce(a.topic,'')) "
            "      @@ plainto_tsquery('english', %s) "
            "   or a.title ilike %s "
            "order by a.created_at desc limit %s", (q, f"%{q}%", limit))
        return cur.fetchall()


def history(article_id: str) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select v.version_no, v.word_count, r.kind, r.model, r.overall, "
            "r.verdict, r.cost_usd, r.created_at "
            "from versions v left join runs r on r.version_id=v.id "
            "where v.article_id=%s order by v.version_no, r.created_at", (article_id,))
        return cur.fetchall()
