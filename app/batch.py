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

# The shadow judge: a cheaper model reviews the same articles, submitted in
# its own batch alongside the real one (OpenAI allows one model per batch).
# Its result is stored in shadow_reviews and never shown — it exists so
# that, after a month of real articles, scripts/shadow_report.py can say
# whether the cheap model would have been good enough. Empty string = off.
SHADOW_MODEL = os.environ.get("PV_SHADOW_MODEL", "gpt-5.6-luna")


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
            "q.completed_at, q.model, b.openai_id, b.seq, b.created_at as sent_at, "
            "(select count(*) from queue_items x where x.batch_id=q.batch_id) as batch_size "
            "from queue_items q left join batches b on b.id = q.batch_id "
            "where q.article_id=%s order by q.created_at desc limit 1", (article_id,))
        return cur.fetchone()


def pull_out(article_id: str) -> dict | None:
    """Take an article out of the queue so it can be reviewed instantly.
    Possible while it is still waiting, or after the queue gave up on it —
    not once it has been sent to OpenAI."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update queue_items set status='failed', error='reviewed instantly instead', "
            "completed_at=now() where article_id=%s and status in ('queued','failed') "
            "returning version_id", (article_id,))
        row = cur.fetchone()
        conn.commit()
    return row


def overview() -> dict:
    """Every queue run, newest first, with the articles in it — what the
    Queue screen shows. Items not yet sent form their own group."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select q.id, q.article_id, a.title, q.status, q.error, q.attempts, q.created_at, "
            "q.submitted_at, q.completed_at, q.batch_id, "
            "(select overall from runs r where r.version_id=q.version_id and r.kind='review' "
            " order by r.created_at desc limit 1) as overall "
            "from queue_items q join articles a on a.id=q.article_id "
            "where q.error is distinct from 'reviewed instantly instead' "
            "order by q.created_at desc limit 500")
        items = cur.fetchall()
        cur.execute("select id, openai_id, status, n_items, created_at, completed_at, error, seq "
                    "from batches where kind='main' order by created_at desc limit 200")
        batches = {str(b["id"]): dict(b, items=[]) for b in cur.fetchall()}
    waiting = []
    for it in items:
        it["overall"] = float(it["overall"]) if it["overall"] is not None else None
        bid = str(it["batch_id"]) if it["batch_id"] else None
        if bid and bid in batches:
            batches[bid]["items"].append(it)
        else:
            waiting.append(it)
    runs = [b for b in batches.values() if b["items"]]
    for b in runs:
        st = [i["status"] for i in b["items"]]
        b["done"] = st.count("done")
        b["failed"] = st.count("failed")
        b["pending"] = st.count("submitted")
    return {"waiting": waiting, "runs": runs}


def requeue(article_ids: list[str] | None = None, batch_id: str | None = None) -> int:
    """Put failed items back in the queue. By batch, by article, or all."""
    with store.connect() as conn, conn.cursor() as cur:
        sql = ("update queue_items set status='queued', batch_id=null, shadow_batch_id=null, "
               "submitted_at=null, completed_at=null, attempts=0, error=null "
               "where status='failed' and error is distinct from 'reviewed instantly instead'")
        args: list = []
        if batch_id:
            sql += " and batch_id=%s"; args.append(batch_id)
        if article_ids:
            sql += " and article_id = any(%s)"; args.append(article_ids)
        cur.execute(sql, args)
        n = cur.rowcount
        conn.commit()
    return n


# ------------------------------------------------------------------ submit

def _create_batch(client, lines: list[str], kind: str, n: int):
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    f = client.files.create(file=(f"reviews-{kind}.jsonl", io.BytesIO(payload)), purpose="batch")
    b = client.batches.create(input_file_id=f.id, endpoint=ENDPOINT,
                              completion_window="24h",
                              metadata={"app": "parentveda-checker", "kind": kind, "n": str(n)})
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into batches (openai_id, status, input_file_id, n_items, kind, seq) "
            "values (%s,%s,%s,%s,%s, case when %s='main' then "
            "(select coalesce(max(seq),0)+1 from batches where kind='main') end) returning id",
            (b.id, b.status, f.id, n, kind, kind))
        batch_id = cur.fetchone()["id"]
        conn.commit()
    log.info("%s batch %s submitted with %d items", kind, b.id, n)
    return batch_id, b.id


def submit() -> dict | None:
    """Everything queued → one batch for the real judge and, if configured,
    one for the shadow. Returns what was submitted, or None if the queue
    was empty."""
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
    main, shadow = [], []
    for it in items:
        system, user = judge.parts(it["body"], catalogue)
        body = engines.openai_request(it["model"], system, user, it["effort"],
                                      judge.MAX_TOKENS, judge.SCHEMA)
        main.append(json.dumps({"custom_id": str(it["id"]), "method": "POST",
                                "url": ENDPOINT, "body": body}, ensure_ascii=False))
        if SHADOW_MODEL and SHADOW_MODEL != it["model"]:
            sbody = engines.openai_request(SHADOW_MODEL, system, user, it["effort"],
                                           judge.MAX_TOKENS, judge.SCHEMA)
            shadow.append(json.dumps({"custom_id": str(it["id"]), "method": "POST",
                                      "url": ENDPOINT, "body": sbody}, ensure_ascii=False))

    client = _client()
    ids = [it["id"] for it in items]
    batch_id, openai_id = _create_batch(client, main, "main", len(items))
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update queue_items set status='submitted', batch_id=%s, submitted_at=now(), "
            "attempts=attempts+1 where id = any(%s)", (batch_id, ids))
        conn.commit()

    out = {"id": batch_id, "openai_id": openai_id, "n": len(items)}
    if shadow:
        try:  # the shadow is a data point; its failure must not touch the real run
            sid, soid = _create_batch(client, shadow, "shadow", len(shadow))
            with store.connect() as conn, conn.cursor() as cur:
                cur.execute("update queue_items set shadow_batch_id=%s where id = any(%s)",
                            (sid, ids))
                conn.commit()
            out["shadow"] = {"id": sid, "openai_id": soid}
        except Exception as exc:
            log.warning("shadow batch not submitted: %s", exc)
            out["shadow_error"] = str(exc)[:300]
    return out


# ------------------------------------------------------------------ poll + ingest

_TERMINAL = ("completed", "expired", "failed", "cancelled")


def poll() -> list[dict]:
    """Check every batch in flight; ingest the ones that finished."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, openai_id, kind from batches where status != all(%s)",
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
        if row["kind"] == "shadow":
            _ingest_shadow(client, row["id"], b)
        else:
            _ingest(client, row["id"], b)
        done.append({"id": row["id"], "kind": row["kind"], "status": b.status})
    return done


def _read_output(client, b) -> tuple[dict, dict]:
    """custom_id → result line, and custom_id → error message."""
    results: dict[str, dict] = {}
    if b.output_file_id:
        for line in client.files.content(b.output_file_id).text.splitlines():
            if line.strip():
                rec = json.loads(line)
                results[rec["custom_id"]] = rec
    errors: dict[str, str] = {}
    if b.error_file_id:
        for line in client.files.content(b.error_file_id).text.splitlines():
            if line.strip():
                rec = json.loads(line)
                err = rec.get("error") or {}
                errors[rec["custom_id"]] = err.get("message") or json.dumps(err)[:300]
    for e in (getattr(b, "errors", None) and b.errors.data) or []:
        # batch-level validation errors apply to every item; say so in words
        # an intern can act on, and keep the code for us
        errors.setdefault("*", f"OpenAI rejected the whole batch before reviewing anything ({e.code}).")
    return results, errors


def _close_batch(batch_id, b) -> None:
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "update batches set status=%s, output_file_id=%s, error_file_id=%s, "
            "completed_at=now(), error=%s where id=%s",
            (b.status, b.output_file_id, b.error_file_id,
             json.dumps(b.errors.model_dump())[:500] if getattr(b, "errors", None) else None,
             batch_id))
        conn.commit()


def _ingest(client, batch_id, b) -> None:
    """Turn a finished batch into runs. Anything without a good result is
    re-queued once, then marked failed with the reason."""
    results, errors = _read_output(client, b)
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, version_id, model, effort, attempts, created_by, created_at "
                    "from queue_items where batch_id=%s and status='submitted'", (batch_id,))
        items = cur.fetchall()

    for it in items:
        cid = str(it["id"])
        rec = results.get(cid)
        try:
            if not rec:
                raise RuntimeError(errors.get(cid) or errors.get("*")
                                   or f"No result came back for this article (batch {b.status}).")
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
    _close_batch(batch_id, b)


def _ingest_shadow(client, batch_id, b) -> None:
    """Store the cheap model's take on each article. Never raises, never
    re-queues: the shadow is a data point, not a dependency."""
    results, errors = _read_output(client, b)
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, version_id, effort from queue_items where shadow_batch_id=%s",
                    (batch_id,))
        items = cur.fetchall()
    from app import engines
    for it in items:
        cid = str(it["id"])
        row = {"overall": None, "verdict": None, "review": {}, "cost": 0.0, "error": None}
        try:
            rec = results.get(cid)
            if not rec:
                raise RuntimeError(errors.get(cid) or errors.get("*") or f"no result (batch {b.status})")
            resp = rec.get("response") or {}
            if resp.get("status_code") != 200:
                raise RuntimeError(errors.get(cid) or f"HTTP {resp.get('status_code')}")
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
            log.exception("shadow review for %s not stored", cid)
    _close_batch(batch_id, b)


def _mark(item_id, status: str, error: str | None = None) -> None:
    with store.connect() as conn, conn.cursor() as cur:
        if status == "queued":       # back for another go: forget the old batch
            cur.execute("update queue_items set status='queued', batch_id=null, "
                        "shadow_batch_id=null, submitted_at=null, error=%s where id=%s",
                        (error, item_id))
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
