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
}


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30,
                           row_factory=dict_row)


def cost_usd(usage: dict) -> float:
    p = PRICING.get(usage["model"])
    if not p:
        return 0.0
    return round(
        usage["in"] * p[0] / 1e6 + usage["out"] * p[1] / 1e6
        + usage.get("cache_write", 0) * p[2] / 1e6
        + usage.get("cache_read", 0) * p[3] / 1e6, 4)


def ruleset_version() -> str:
    text = content.get("ruleset")
    return "v1-" + hashlib.sha256(text.encode()).hexdigest()[:8]


def save_review(title: str, body: str, review: dict, *, author: str | None = None,
                article_id: str | None = None, kind: str = "review",
                source: str = "upload", actor: str | None = None) -> dict[str, Any]:
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

        cur.execute(
            "insert into runs (version_id, kind, model, effort, ruleset_version, "
            "overall, verdict, blockers, article_level, expert_review, "
            "tokens_in, tokens_out, cache_write, cache_read, cost_usd, actor_email) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (version_id, kind, u["model"], u.get("effort", "high"), ruleset_version(),
             review["overall"], review["verdict"], json.dumps(review["blockers"]),
             json.dumps(review["article_level"]), json.dumps(review["expert_review"]),
             u["in"], u["out"], u.get("cache_write", 0), u.get("cache_read", 0),
             cost_usd(u), actor))
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
        conn.commit()
    return {"article_id": article_id, "version_id": version_id,
            "run_id": run_id, "version_no": vno}


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
