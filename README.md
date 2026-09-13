# ParentVeda Article Checker

Reviews a draft against the ParentVeda editorial standard, applies the changes a
human accepts, and produces a verification sheet for a clinician.

Replaces the manual paste-into-ChatGPT loop: upload, decide, download.

## What it does

```
upload ─► review ─► accept / reject ─► apply ─► export ─► images ─► doctor sheet ─► verified
```

- Scores against **12 weighted parameters** from the review framework. The
  overall is computed in code, never guessed by the model.
- **Blocks publication** when medical accuracy or safety falls below 8.5, or the
  draft substantially duplicates a published article.
- Detects overlap against the **24 live articles** on parentveda.in.
- Applies only the findings a human accepted — never its own opinions.
  Sentence-level findings are exact swaps and cost nothing; only changes to
  the article's shape go to a model. Every finding shows which it is before
  anyone accepts it, and the Apply button says what it will cost.
- No automatic re-score. The reviewer wrote the changes; re-reading them
  buys nothing. A priced **Re-score** button exists for anyone who wants a
  fresh number.
- Writes **image briefs** — a cover and up to two in-article visuals as
  paste-ready prompts. Prompts only; no images are generated.
- Extracts every clinical claim, number and red flag into a one-page
  verification sheet, routed to the right expert on the roster.
- Keeps every version, score, decision and cost, searchable, shared across the
  whole team.

## Running it

```bash
pip install -r requirements.txt
uvicorn app.api:app --port 8420
```

Then open <http://localhost:8420>.

### Environment

Put these in `.env` (never committed):

| Variable | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `DATABASE_URL` | Supabase session-pooler connection string |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | The publishable key — never the service-role key |
| `PV_REQUIRE_AUTH` | Set to `0` to bypass Google sign-in locally |
| `PV_ENGINE` | `claude` (default) or `openai`; the UI can switch per browser |
| `PV_MODEL_SCORE` / `_EDIT` / `_DOCTOR` / `_IMAGES` | Claude models; default Opus 5 scores, Sonnet 5 for the rest |
| `PV_OPENAI_MODEL_SCORE` / `_EDIT` / `_DOCTOR` / `_IMAGES` | OpenAI models; default gpt-5 scores, gpt-5.6-luna for the rest |
| `PV_INR_RATE` | Rupees per dollar for the prices the screen shows; default 95 |

### Models

Two engines, same prompts, same schema. Measured on the same draft:

| Step | Claude | ChatGPT |
|---|---|---|
| Review (scores + findings) | Opus 5 · ~$0.30 | gpt-5 · ~$0.13 |
| Apply (structural changes only) | Sonnet 5 · ~$0.10 | gpt-5.6-luna · ~$0.005 |
| Doctor sheet | Sonnet 5 · ~$0.05 | gpt-5.6-luna · ~$0.004 |
| Image briefs | Sonnet 5 · ~$0.02 | gpt-5.6-luna · ~$0.001 |

gpt-5 matched Opus finding for finding on the calibration article; luna
applied a five-item brief faithfully and wrote a complete sheet. Roughly
**$0.14 per article** on ChatGPT, **$0.45** on Claude, end to end.

## Before your first push

```bash
git config core.hooksPath .githooks
```

That makes every commit run `scripts/check_secrets.py` first and refuse if a
key or any editorial content would go up. The repo is public; `.env` holds a
live API key and the database password.

## Layout

```
app/
  api.py          FastAPI routes
  judge.py        scoring — reads only, never edits
  editor.py       applies accepted findings — scores nothing
  doctor.py       the clinician verification sheet
  auth.py         Google sign-in via Supabase
  store.py        persistence; every run pins model, effort, ruleset version
  export.py       DOCX and printable HTML
  prompts/        judge, editor, doctor
corpus/
  source/         the Bible, extracted verbatim
  compiled/       the operative ruleset the model actually reads
web/              the app — one page, no build step
scripts/          catalogue scraper, DB setup, checks
```

## The compiled ruleset

`corpus/compiled/parentveda-ruleset.md` is the Editorial Handbook, Publishing
Bible, Design Playbook and review framework reduced to operative rules —
18,500 tokens down to 3,400, every rule kept, the prose dropped. It is sent
with every call and cached.

Edit that file to change how articles are judged. Every run records which
version produced it, so an old score stays explicable.
