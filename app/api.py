"""FastAPI backend. Serves the app and the endpoints behind it."""
import re
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from fastapi.responses import HTMLResponse, Response

from app import (auth, config, content, doctor, editor, export, judge,
                 parse, store)

WEB = config.ROOT / "web"

app = FastAPI(title="ParentVeda Article Checker")


# ------------------------------------------------------------------ articles

@app.post("/api/review")
async def review(file: UploadFile | None = File(None),
                 text: str | None = Form(None),
                 title: str | None = Form(None),
                 author: str | None = Form(None),
                 who: str = Depends(auth.actor)):
    """Upload or paste an article, score it, persist everything."""
    if file is not None:
        body = parse.from_bytes(file.filename, await file.read())
    elif text:
        body = text.strip()
    else:
        raise HTTPException(400, "Send a file or some text.")

    # Everything from the first production heading onward is internal and is
    # stored but never reviewed — reviewing it produced findings about the
    # team's own scaffolding rather than about the article.
    body, internal = parse.split_internal(body)

    if len(body.split()) < 120:
        raise HTTPException(400, "That looks too short to review — under 120 words.")

    # judge.review blocks for minutes. On the event loop that freezes every
    # other request, so it runs in a worker thread instead.
    result = await run_in_threadpool(
        judge.review, body, content.catalogue(), config.MODELS["score"])
    ids = store.save_review(title or parse.guess_title(body, file.filename if file else "Untitled"),
                            body, result, author=author, actor=who)
    return {**ids,
            "review": _shape(result, ids["run_id"]),
            "internal_words": len(internal.split())}


def _shape(result: dict, run_id: str) -> dict:
    """Trim the model payload to what the screen actually renders."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, tier, kind, parameter, summary, quote, proposed, "
                    "rationale, position from feedback where run_id=%s order by position",
                    (run_id,))
        items = cur.fetchall()
    return {
        "overall": result["overall"],
        "verdict": result["verdict"],
        "blockers": result["blockers"],
        "article_type": result["article_type"],
        "article_level": result["article_level"],
        "expert_review": result["expert_review"],
        "scores": [
            {"parameter": p, "label": config.PARAMETER_LABELS[p],
             "weight": config.WEIGHTS[p], **v}
            for p, v in result["scores"].items()
        ],
        "feedback": items,
        "usage": result["_usage"],
        "cost": store.cost_usd(result["_usage"]),
    }


@app.get("/api/articles")
def articles(q: str = "", status: str = "", limit: int = 50,
             who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        sql = ["select a.id, a.title, a.status, a.article_type, a.author, a.created_at,",
               "  (select overall from runs r join versions v on v.id=r.version_id",
               "   where v.article_id=a.id order by r.created_at desc limit 1) as score,",
               "  (select word_count from versions v where v.article_id=a.id",
               "   order by version_no desc limit 1) as words,",
               "  (select verdict from runs r join versions v on v.id=r.version_id",
               "   where v.article_id=a.id order by r.created_at desc limit 1) as verdict",
               "from articles a where true"]
        args: list = []
        if q:
            sql.append("and (a.title ilike %s or a.topic ilike %s)")
            args += [f"%{q}%", f"%{q}%"]
        if status:
            sql.append("and a.status = %s")
            args.append(status)
        sql.append("order by a.created_at desc limit %s")
        args.append(limit)
        cur.execute(" ".join(sql), args)
        return cur.fetchall()


@app.get("/api/articles/{article_id}")
def article_detail(article_id: str, who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select * from articles where id=%s", (article_id,))
        art = cur.fetchone()
        if not art:
            raise HTTPException(404, "No such article.")
        cur.execute(
            "select r.id, r.kind, r.model, r.effort, r.overall, r.verdict, r.cost_usd,"
            " r.created_at, r.blockers, r.article_level, r.expert_review, r.actor_email,"
            " v.version_no, v.word_count "
            "from runs r join versions v on v.id = r.version_id "
            "where v.article_id=%s order by r.created_at", (article_id,))
        runs = cur.fetchall()
        cur.execute("select * from verification where article_id=%s order by created_at",
                    (article_id,))
        ver = cur.fetchall()
        cur.execute(
            "select f.tier, count(*) filter (where d.outcome in ('accepted','edited')) as accepted,"
            " count(*) filter (where d.outcome='rejected') as rejected, count(*) as total "
            "from feedback f join decisions d on d.feedback_id=f.id "
            "join runs r on r.id=f.run_id join versions v on v.id=r.version_id "
            "where v.article_id=%s group by f.tier", (article_id,))
        tiers = cur.fetchall()
    return {"article": art, "runs": runs, "verification": ver, "decisions": tiers}


@app.get("/api/articles/{article_id}/trail")
def article_trail(article_id: str, who: str = Depends(auth.actor)):
    """Everything that happened to this article, grouped by version.

    One call, so the screen can show a single expandable trail rather than
    making someone click through versions to reconstruct the story.
    """
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, title, status, article_type, author, created_by, "
                    "created_at from articles where id=%s", (article_id,))
        art = cur.fetchone()
        if not art:
            raise HTTPException(404, "No such article.")

        cur.execute(
            "select id, version_no, body, word_count, source, created_at "
            "from versions where article_id=%s order by version_no", (article_id,))
        versions = cur.fetchall()

        cur.execute(
            "select r.id, r.version_id, r.kind, r.model, r.effort, r.overall, "
            "r.verdict, r.cost_usd, r.actor_email, r.created_at, r.blockers "
            "from runs r join versions v on v.id = r.version_id "
            "where v.article_id=%s order by r.created_at", (article_id,))
        runs = cur.fetchall()

        cur.execute(
            "select f.id, f.run_id, f.tier, f.kind, f.parameter, f.summary, "
            "f.quote, f.proposed, f.rationale, f.position, "
            "d.outcome, d.decided_by, d.decided_at, d.auto, d.edited_text "
            "from feedback f join runs r on r.id = f.run_id "
            "join versions v on v.id = r.version_id "
            "left join decisions d on d.feedback_id = f.id "
            "where v.article_id=%s order by f.position", (article_id,))
        feedback = cur.fetchall()

        cur.execute("select * from verification where article_id=%s "
                    "order by created_at", (article_id,))
        verification = cur.fetchall()

    by_run: dict = {}
    for f in feedback:
        by_run.setdefault(str(f["run_id"]), []).append(f)
    for r in runs:
        r["feedback"] = by_run.get(str(r["id"]), [])
        r["accepted"] = sum(1 for f in r["feedback"]
                            if f["outcome"] in ("accepted", "edited"))
        r["rejected"] = sum(1 for f in r["feedback"] if f["outcome"] == "rejected")

    by_version: dict = {}
    for r in runs:
        by_version.setdefault(str(r["version_id"]), []).append(r)
    for v in versions:
        v["runs"] = by_version.get(str(v["id"]), [])

    return {"article": art, "versions": versions, "verification": verification}


# ------------------------------------------------------------------ decisions

class Decision(BaseModel):
    feedback_id: str
    outcome: str
    edited_text: str | None = None
    by: str | None = None
    auto: bool = False


@app.post("/api/decide")
def decide(d: Decision, who: str = Depends(auth.actor)):
    if d.outcome not in ("accepted", "rejected", "edited", "pending"):
        raise HTTPException(400, "outcome must be accepted, rejected, edited or pending")
    store.decide(d.feedback_id, d.outcome, by=who, edited=d.edited_text, auto=d.auto)
    return {"ok": True}


class BulkDecision(BaseModel):
    run_id: str
    tiers: list[str] = ["must", "should"]
    by: str | None = None


@app.post("/api/decide-all")
def decide_all(b: BulkDecision, who: str = Depends(auth.actor)):
    """'Choose for me' — accept the named tiers, reject the rest."""
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select id, tier from feedback where run_id=%s", (b.run_id,))
        rows = cur.fetchall()
    for r in rows:
        store.decide(r["id"], "accepted" if r["tier"] in b.tiers else "rejected",
                     by=who, auto=True)
    return {"decided": len(rows)}


# ------------------------------------------------------------------ rewrite

class RewriteReq(BaseModel):
    run_id: str


@app.post("/api/rewrite")
def rewrite(req: RewriteReq, who: str = Depends(auth.actor)):
    """Apply the accepted findings, then re-score the result cold."""
    accepted = store.accepted_feedback(req.run_id)
    if not accepted:
        raise HTTPException(400, "Nothing accepted yet — accept at least one finding.")

    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select v.body, v.article_id, a.title from runs r "
            "join versions v on v.id = r.version_id "
            "join articles a on a.id = v.article_id where r.id=%s", (req.run_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "No such run.")

    result = editor.rewrite(row["body"], [dict(f) for f in accepted],
                            model=config.MODELS["edit"])

    rescore = judge.review(result["body"], catalogue=content.catalogue(),
                           model=config.MODELS["score"])
    ids = store.save_review(row["title"], result["body"], rescore,
                            article_id=str(row["article_id"]),
                            kind="verify", source="rewrite", actor=who)

    edit_cost = store.cost_usd(result["_usage"])
    return {
        **ids,
        "diff": result["diff"],
        "body": result["body"],
        "applied": len(accepted),
        "edit_usage": result["_usage"],
        "edit_cost": edit_cost,
        "review": _shape(rescore, ids["run_id"]),
        "total_cost": round(edit_cost + store.cost_usd(rescore["_usage"]), 4),
    }


# ------------------------------------------------------------------ doctor sheet

class SheetReq(BaseModel):
    article_id: str


def _latest(article_id: str) -> dict:
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select a.title, v.body, r.article_level, r.expert_review, r.verdict "
            "from runs r join versions v on v.id=r.version_id "
            "join articles a on a.id=v.article_id where v.article_id=%s "
            "order by r.created_at desc limit 1", (article_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Nothing reviewed for that article yet.")
    return row


@app.post("/api/doctor-sheet")
def doctor_sheet(req: SheetReq, who: str = Depends(auth.actor)):
    row = _latest(req.article_id)
    res = doctor.sheet(row["body"], row["title"],
                       {"expert_review": row["expert_review"],
                        "article_type": None})
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into verification (article_id, specialty, sheet_text) "
            "values (%s,%s,%s) returning id",
            (req.article_id, res["specialty"], res["body"]))
        vid = cur.fetchone()["id"]
        cur.execute("select * from experts where %s = any(specialties) "
                    "and onboarded order by name", (res["specialty"],))
        matches = cur.fetchall()
        conn.commit()
    return {"verification_id": vid, "specialty": res["specialty"],
            "sheet": res["body"], "experts": matches,
            "cost": store.cost_usd(res["_usage"])}


class SendReq(BaseModel):
    verification_id: str
    expert_ids: list[str]


@app.post("/api/send-verification")
def send_verification(req: SendReq, who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select article_id, specialty, sheet_text from verification "
                    "where id=%s", (req.verification_id,))
        base = cur.fetchone()
        if not base:
            raise HTTPException(404, "No such verification.")
        names = []
        for eid in req.expert_ids:
            cur.execute("select name from experts where id=%s and onboarded", (eid,))
            e = cur.fetchone()
            if not e:
                continue
            names.append(e["name"])
            cur.execute(
                "insert into verification (article_id, specialty, doctor_name, "
                "expert_id, sheet_text, sent_at, sent_by) "
                "values (%s,%s,%s,%s,%s, now(), %s)",
                (base["article_id"], base["specialty"], e["name"], eid,
                 base["sheet_text"], who))
        cur.execute("delete from verification where id=%s", (req.verification_id,))
        cur.execute("update articles set status='sent_for_verification', "
                    "updated_at=now() where id=%s", (base["article_id"],))
        conn.commit()
    if not names:
        raise HTTPException(400, "Pick at least one onboarded expert.")
    return {"sent_to": names}


class VerifyReq(BaseModel):
    verification_id: str
    notes: str | None = None


@app.post("/api/mark-verified")
def mark_verified(req: VerifyReq, who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("update verification set verified_at=now(), notes=%s, "
                    "verified_by=%s where id=%s returning article_id",
                    (req.notes, who, req.verification_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No such verification.")
        cur.execute("update articles set status='verified', updated_at=now() "
                    "where id=%s", (row["article_id"],))
        conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ export

DOCX_TYPE = ("application/vnd.openxmlformats-officedocument"
             ".wordprocessingml.document")


def _download(data: bytes, name: str, media: str) -> Response:
    return Response(data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(len(data)),
    })


@app.get("/api/export/{article_id}.docx")
def export_docx(article_id: str):
    row = _latest(article_id)
    data = export.to_docx(row["title"], row["body"],
                          subtitle="ParentVeda · reviewed draft")
    return _download(data, export.filename(row["title"], "docx"), DOCX_TYPE)


@app.get("/api/export/{article_id}.pdf")
def export_pdf(article_id: str):
    """A real PDF, straight to the browser's downloads. No print dialog."""
    row = _latest(article_id)
    data = export.to_pdf(row["title"], row["body"],
                         subtitle="ParentVeda · reviewed draft")
    return _download(data, export.filename(row["title"], "pdf"), "application/pdf")


@app.get("/api/export/{article_id}.md")
def export_md(article_id: str):
    row = _latest(article_id)
    return _download(row["body"].encode("utf-8"),
                     export.filename(row["title"], "md"), "text/markdown")


@app.get("/api/export/{article_id}.html")
def export_html(article_id: str):
    row = _latest(article_id)
    return HTMLResponse(export.to_html(row["title"], row["body"],
                                       subtitle="ParentVeda · reviewed draft"))


@app.get("/api/sheet/{verification_id}.docx")
def sheet_docx(verification_id: str):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select v.sheet_text, v.specialty, a.title from verification v "
                    "join articles a on a.id=v.article_id where v.id=%s",
                    (verification_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "No such sheet.")
    title = f"Verification sheet — {row['title']}"
    data = export.to_docx(title, row["sheet_text"],
                          subtitle=f"For a {row['specialty']} reviewer",
                          footer=export.sheet_footer(row["specialty"]))
    return _download(data, export.filename(f"{row['title']} verification sheet", "docx"),
                     DOCX_TYPE)


@app.get("/api/sheet/{verification_id}.pdf")
def sheet_pdf(verification_id: str):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute("select v.sheet_text, v.specialty, a.title from verification v "
                    "join articles a on a.id=v.article_id where v.id=%s",
                    (verification_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "No such sheet.")
    data = export.to_pdf(f"Verification sheet — {row['title']}", row["sheet_text"],
                         subtitle=f"For a {row['specialty']} reviewer",
                         footer=export.sheet_footer(row["specialty"]))
    return _download(data, export.filename(f"{row['title']} verification sheet", "pdf"),
                     "application/pdf")


# ------------------------------------------------------------------ experts

@app.get("/api/experts")
def experts(specialty: str = "", who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        if specialty:
            cur.execute("select * from experts where %s = any(specialties) "
                        "order by onboarded desc, name", (specialty,))
        else:
            cur.execute("select * from experts order by onboarded desc, name")
        return cur.fetchall()


class Expert(BaseModel):
    name: str
    designation: str | None = None
    specialties: list[str] = []
    practice: str | None = None
    email: str | None = None
    onboarded: bool = True


@app.post("/api/experts")
def add_expert(e: Expert, who: str = Depends(auth.actor)):
    with store.connect() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into experts (name, designation, specialties, practice, email, onboarded) "
            "values (%s,%s,%s,%s,%s,%s) on conflict (name) do update set "
            "designation=excluded.designation, specialties=excluded.specialties, "
            "practice=excluded.practice, email=excluded.email, "
            "onboarded=excluded.onboarded returning id",
            (e.name, e.designation, e.specialties, e.practice, e.email, e.onboarded))
        conn.commit()
        return {"id": cur.fetchone()["id"]}


# ------------------------------------------------------------------ the page

@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/app.js")
def appjs():
    return FileResponse(WEB / "app.js", media_type="application/javascript")


@app.get("/style.css")
def style():
    return FileResponse(WEB / "style.css", media_type="text/css")


@app.get("/logo.png")
def logo():
    return FileResponse(WEB / "logo.png", media_type="image/png")


@app.get("/logo@2x.png")
def logo2x():
    return FileResponse(WEB / "logo@2x.png", media_type="image/png")


@app.get("/favicon.ico")
def favicon():
    return FileResponse(WEB / "favicon.ico", media_type="image/x-icon")


@app.get("/favicon-{size}.png")
def favicon_png(size: str):
    path = WEB / f"favicon-{size}.png"
    if not path.exists():
        raise HTTPException(404, "No icon that size.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/config")
def client_config():
    return auth.public_config()


@app.get("/api/me")
def me(who: str = Depends(auth.actor)):
    return {"email": who}


@app.get("/health")
def health():
    """Enough to diagnose a deploy without exposing anything.

    Reports whether the editorial content loaded, and from where — the
    deployed app carries none of it on disk, so this is the check that
    matters after a fresh deploy.
    """
    import hashlib

    out: dict = {"ok": True, "models": config.MODELS, "effort": config.EFFORT}

    items = {}
    for name in ("ruleset", "judge", "editor", "doctor"):
        try:
            body = content.get(name)
            on_disk = content._FALLBACK[name].exists()
            items[name] = {
                "loaded": True,
                "source": "local file" if on_disk else "database",
                "chars": len(body),
                "sha": hashlib.sha256(body.encode()).hexdigest()[:8],
            }
        except Exception as exc:
            out["ok"] = False
            items[name] = {"loaded": False, "error": str(exc)[:160]}
    out["content"] = items

    try:
        with store.connect() as conn, conn.cursor() as cur:
            cur.execute("select count(*) as n from published")
            out["catalogue_articles"] = cur.fetchone()["n"]
            cur.execute("select count(*) as n from articles")
            out["articles"] = cur.fetchone()["n"]
            cur.execute("select count(*) as n from experts where onboarded")
            out["experts_onboarded"] = cur.fetchone()["n"]
        out["database"] = "connected"
    except Exception as exc:
        out["ok"] = False
        out["database"] = f"unreachable: {str(exc)[:120]}"

    return out
