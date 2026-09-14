"""Batched reviews through OpenAI's Batch API — half price, results within
24 hours, usually well under an hour.

An upload into the queue becomes a queue_item. A background tick every few
minutes submits whatever is queued as one batch, polls batches in flight,
and turns finished output into ordinary runs via judge.finish() — so a
batched review is indistinguishable from an instant one everywhere else
in the app. State lives in Postgres; a redeploy mid-flight loses nothing.

ChatGPT only. The request body is exactly what the instant path sends.
"""
import asyncio
import io
import json
import logging
import os
import time
from datetime import datetime, timezone

from app import config, content, judge, store

log = logging.getLogger("pv.batch")

TICK_SECONDS = int(os.environ.get("PV_BATCH_TICK", "300"))
MAX_ATTEMPTS = 2          # a second try after an expiry or transient failure
ENDPOINT = "/v1/responses"

# The shadow judge: a cheaper model reviews the same article in the same
# batch. Its result is stored in shadow_reviews and never shown — it exists
# so that, after a month of real articles, scripts/shadow_report.py can say
# whether the cheap model would have been good enough. Empty string = off.
SHADOW_MODEL = os.environ.get("PV_SHADOW_MODEL", "gpt-5.6-luna")
SHADOW_SUFFIX = ":shadow"


def _client():
    from openai import OpenAI
    return OpenAI(timeout=120, max_retries=2)


def enabled() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY")) and bool(os.environ.get("DATABASE_URL"))


# ------------------------------------------------------------------ enqueue

def enqueue(title: str, body: str, *, author: str | None, actor: str | None) -> dict:
    """Store the draft and put it in the queue. No model call happens here."""
    model = config.ENGINES["openai"]["score"]
    ids = store.save_draft(title, body, author=author, actor=actor)
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into queue_items (article_id, version_id, model, effort, created_by) "
            "values (%s,%s,%s,%s,%s) returning id, created_at",
            (ids["article_id"], ids["version_id"], model, config.EFFORT, actor))
        row = cur.fetchone()
        conn.commit()
    return {**ids, "queue_id": row["id"], "queued_at": row["created_at"], "model": model}


def status_for(article_id: str) -> dict | None:
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select q.id, q.status, q.attempts, q.error, q.created_at, q.submitted_at, "
            "q.completed_at, q.model, b.openai_id from queue_items q "
            "left join batches b on b.id = q.batch_id "
            "where q.article_id=%s order by q.created_at desc limit 1", (article_id,))
        return cur.fetchone()


def pull_out(article_id: str) -> dict | None:
    """Take an article out of the queue so it can be reviewed instantly.
    Only possible while it is still queued (not yet sent to OpenAI)."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update queue_items set status='failed', error='reviewed instantly instead', "
            "completed_at=now() where article_id=%s and status='queued' "
            "returning version_id", (article_id,))
        row = cur.fetchone()
        conn.commit()
    return row


# ------------------------------------------------------------------ submit

def submit() -> dict | None:
    """Everything queued → one batch. Returns the batch row, or None if the
    queue was empty."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select q.id, q.model, q.effort, v.body from queue_items q "
            "join versions v on v.id = q.version_id "
            "where q.status='queued' order by q.created_at limit 500")
        items = cur.fetchall()
    if not items:
        return None

    from app import engines
    catalogue = content.catalogue()
    lines = []
    for it in items:
        system, user = judge.parts(it["body"], catalogue)
        body = engines.openai_request(it["model"], system, user, it["effort"],
                                      judge.MAX_TOKENS, judge.SCHEMA)
        lines.append(json.dumps({"custom_id": str(it["id"]), "method": "POST",
                                 "url": ENDPOINT, "body": body}, ensure_ascii=False))
        if SHADOW_MODEL and SHADOW_MODEL != it["model"]:
            shadow = engines.openai_request(SHADOW_MODEL, system, user, it["effort"],
                                            judge.MAX_TOKENS, judge.SCHEMA)
            lines.append(json.dumps({"custom_id": str(it["id"]) + SHADOW_SUFFIX,
                                     "method": "POST", "url": ENDPOINT, "body": shadow},
                                    ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8")

    client = _client()
    f = client.files.create(file=("reviews.jsonl", io.BytesIO(payload)), purpose="batch")
    b = client.batches.create(input_file_id=f.id, endpoint=ENDPOINT,
                              completion_window="24h",
                              metadata={"app": "parentveda-checker", "n": str(len(items))})

    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into batches (openai_id, status, input_file_id, n_items) "
            "values (%s,%s,%s,%s) returning id", (b.id, b.status, f.id, len(items)))
        batch_id = cur.fetchone()["id"]
        cur.execute(
            "update queue_items set status='submitted', batch_id=%s, submitted_at=now(), "
            "attempts=attempts+1 where id = any(%s)",
            (batch_id, [it["id"] for it in items]))
        conn.commit()
    log.info("batch %s submitted with %d items", b.id, len(items))
    return {"id": batch_id, "openai_id": b.id, "n": len(items)}


# ------------------------------------------------------------------ poll + ingest

_TERMINAL = ("completed", "expired", "failed", "cancelled")


def poll() -> list[dict]:
    """Check every batch in flight; ingest the ones that finished."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, openai_id from batches where status != all(%s)",
                    (list(_TERMINAL),))
        open_batches = cur.fetchall()
    if not open_batches:
        return []

    client = _client()
    done = []
    for row in open_batches:
        b = client.batches.retrieve(row["openai_id"])
        if b.status not in _TERMINAL:
            with store.connect() as conn, conn.cursor() as cur:
                cur.execute("update batches set status=%s where id=%s", (b.status, row["id"]))
                conn.commit()
            continue
        _ingest(client, row["id"], b)
        done.append({"id": row["id"], "status": b.status})
    return done


def _ingest(client, batch_id, b) -> None:
    """Turn a finished batch into runs. Anything without a good result is
    re-queued once, then marked failed with the reason."""
    results: dict[str, dict] = {}
    if b.output_file_id:
        text = client.files.content(b.output_file_id).text
        for line in text.splitlines():
            if line.strip():
                rec = json.loads(line)
                results[rec["custom_id"]] = rec
    errors: dict[str, str] = {}
    if b.error_file_id:
        text = client.files.content(b.error_file_id).text
        for line in text.splitlines():
            if line.strip():
                rec = json.loads(line)
                err = rec.get("error") or {}
                errors[rec["custom_id"]] = err.get("message") or json.dumps(err)[:300]

    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, version_id, model, effort, attempts, created_by, created_at "
                    "from queue_items where batch_id=%s and status='submitted'", (batch_id,))
        items = cur.fetchall()

    for it in items:
        cid = str(it["id"])
        _shadow(it, results.get(cid + SHADOW_SUFFIX), errors.get(cid + SHADOW_SUFFIX))
        rec = results.get(cid)
        try:
            if not rec:
                raise RuntimeError(errors.get(cid) or f"no result (batch {b.status})")
            resp = rec.get("response") or {}
            if resp.get("status_code") != 200:
                raise RuntimeError(errors.get(cid) or f"HTTP {resp.get('status_code')}: "
                                   f"{json.dumps(resp.get('body'))[:300]}")
            from app import engines
            parsed = engines.parse_openai(resp["body"], it["model"], it["effort"])
            parsed["usage"]["batch"] = True
            review = judge.finish(parsed)
            waited = (datetime.now(timezone.utc) - it["created_at"]).total_seconds()
            store.attach_run(str(it["version_id"]), review, actor=it["created_by"],
                             duration_s=round(waited, 1), kind="review")
            _mark(it["id"], "done")
        except Exception as exc:  # one item failing must not sink the batch
            log.warning("queue item %s: %s", cid, exc)
            if it["attempts"] < MAX_ATTEMPTS:
                _mark(it["id"], "queued", error=str(exc)[:500])
            else:
                _mark(it["id"], "failed", error=str(exc)[:500])

    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update batches set status=%s, output_file_id=%s, error_file_id=%s, "
            "completed_at=now(), error=%s where id=%s",
            (b.status, b.output_file_id, b.error_file_id,
             json.dumps(b.errors.model_dump()) [:500] if getattr(b, "errors", None) else None,
             batch_id))
        conn.commit()


def _shadow(it, rec, err) -> None:
    """Store the cheap model's take on the same article. Never raises: the
    shadow is a data point, not a dependency."""
    if not SHADOW_MODEL or (rec is None and err is None):
        return
    row = {"overall": None, "verdict": None, "review": {}, "cost": 0.0, "error": None}
    try:
        if not rec:
            raise RuntimeError(err or "no shadow result")
        resp = rec.get("response") or {}
        if resp.get("status_code") != 200:
            raise RuntimeError(err or f"HTTP {resp.get('status_code')}")
        from app import engines
        parsed = engines.parse_openai(resp["body"], SHADOW_MODEL, it["effort"])
        parsed["usage"]["batch"] = True
        review = judge.finish(parsed)
        row.update(overall=review["overall"], verdict=review["verdict"],
                   cost=store.cost_usd(review["_usage"]),
                   review={k: v for k, v in review.items() if not k.startswith("_")})
    except Exception as exc:
        row["error"] = str(exc)[:500]
    try:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "insert into shadow_reviews (version_id, model, overall, verdict, review, "
                "cost_usd, error) values (%s,%s,%s,%s,%s,%s,%s)",
                (it["version_id"], SHADOW_MODEL, row["overall"], row["verdict"],
                 json.dumps(row["review"], ensure_ascii=False), row["cost"], row["error"]))
            conn.commit()
    except Exception:
        log.exception("shadow review for %s not stored", it["id"])


def _mark(item_id, status: str, error: str | None = None) -> None:
    with store.connect() as conn, conn.cursor() as cur:
        if status == "queued":       # back for another go: forget the old batch
            cur.execute("update queue_items set status='queued', batch_id=null, "
                        "submitted_at=null, error=%s where id=%s", (error, item_id))
        else:
            cur.execute("update queue_items set status=%s, error=%s, completed_at=now() "
                        "where id=%s", (status, error, item_id))
        conn.commit()


# ------------------------------------------------------------------ the loop

def tick() -> dict:
    """One pass: poll what is in flight, then submit what is waiting."""
    out = {"polled": [], "submitted": None}
    try:
        out["polled"] = poll()
    except Exception as exc:
        log.exception("poll failed")
        out["poll_error"] = str(exc)[:300]
    try:
        out["submitted"] = submit()
    except Exception as exc:
        log.exception("submit failed")
        out["submit_error"] = str(exc)[:300]
    return out


async def loop() -> None:
    """Runs for the life of the process. Errors are logged, never fatal."""
    from fastapi.concurrency import run_in_threadpool
    await asyncio.sleep(15)                 # let the app finish starting
    while True:
        t0 = time.monotonic()
        try:
            await run_in_threadpool(tick)
        except Exception:
            log.exception("batch tick crashed")
        await asyncio.sleep(max(30, TICK_SECONDS - (time.monotonic() - t0)))
