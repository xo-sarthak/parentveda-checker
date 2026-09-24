# ParentVeda Article Checker

Reviews a draft against the ParentVeda editorial standard, applies the changes a
human accepts, and produces a verification sheet for a clinician.

Replaces the manual paste-into-ChatGPT loop: upload, decide, download.

Live at <https://checker.parentveda.in> — Google sign-in, one shared workspace,
every run stamped with the email that started it.

## What it does

```
upload ─► batch ─► review ─► accept / reject ─► apply ─► export ─► images ─► doctor sheet ─► verified
```

- Scores against **12 weighted parameters** from the review framework. The
  overall is computed in code, never guessed by the model.
- **Blocks publication** when medical accuracy or safety falls below 8.5, or the
  draft substantially duplicates a published article.
- Detects overlap against the **24 live articles** on parentveda.in.
- **Batch or review now.** Drop any number of articles, send them as one
  numbered batch through OpenAI's Batch API at half price — usually back
  within the hour, always within a day. Nobody keeps a tab open. A batch that
  fails goes again in one click, as a whole. *Review now* does a single
  article in about 90 seconds at full price, for when someone is waiting.
- Applies only the findings a human accepted — never its own opinions.
  Sentence-level findings are exact swaps and cost nothing; only changes to
  the article's shape go to a model. Every finding shows which it is before
  anyone accepts it, and the Apply button says what it will cost.
- No automatic re-score. The reviewer wrote the changes; re-reading them
  buys nothing.
- Writes **image briefs** — a cover and up to two in-article visuals as
  paste-ready prompts. Prompts only; no images are generated.
- **Shadow judge.** Every batched article is also reviewed by a cheaper model,
  in its own batch. Stored, never shown; `python scripts/shadow_report.py`
  says whether the cheap model could take over.
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
| `OPENAI_API_KEY` | From platform.openai.com. Also enables batching. |
| `ANTHROPIC_API_KEY` | From console.anthropic.com — only for the Claude engine |
| `DATABASE_URL` | Supabase session-pooler connection string |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | The publishable key — never the service-role key |
| `PV_REQUIRE_AUTH` | Set to `0` to bypass Google sign-in locally |
| `PV_ENGINE` | `openai` (production) or `claude`; the UI can switch per browser |
| `PV_OPENAI_MODEL_SCORE` / `_EDIT` / `_DOCTOR` / `_IMAGES` | Defaults: `gpt-5` scores, `gpt-5.6-luna` for the rest |
| `PV_MODEL_SCORE` / `_EDIT` / `_DOCTOR` / `_IMAGES` | Claude models; defaults Opus 5 scores, Sonnet 5 for the rest |
| `PV_INR_RATE` | Rupees per dollar for the prices the screen shows; default 95 |
| `PV_BATCH_TICK` | Seconds between batch passes; default 300 |
| `PV_BATCH_LOOP` | `0` disables the background batch loop (local testing) |
| `PV_SHADOW_MODEL` | Second model run on every batch; default `gpt-5.6-luna`, empty to disable |

### Models

**Production runs on ChatGPT** (`PV_ENGINE=openai`): gpt-5 reviews, luna does
everything after. Claude is still wired up and switchable — same prompts, same
schema — but it costs three times as much for the same findings.

Follow-up steps inherit the engine that produced the review, so a gpt-5 review
is never applied by Sonnet because a browser was left on the wrong setting.

| Step | ChatGPT (default) | Claude |
|---|---|---|
| Review — scores + findings | gpt-5 · ~$0.13 (~$0.065 batched) | Opus 5 · ~$0.30 |
| Apply — structural changes only | gpt-5.6-luna · ~$0.005 | Sonnet 5 · ~$0.10 |
| Doctor sheet | gpt-5.6-luna · ~$0.004 | Sonnet 5 · ~$0.05 |
| Image briefs | gpt-5.6-luna · ~$0.001 | Sonnet 5 · ~$0.02 |
| Shadow judge (batch only) | gpt-5.6-luna · ~$0.005 | — |

gpt-5 matched Opus finding for finding on the calibration articles at half the
price; luna applies a brief faithfully and writes a complete sheet for under a
cent. **Batched, end to end, an article costs about ₹7** — roughly ₹1,100 a
month at 150 articles, ₹1,750 at 240.

## Before your first push

```bash
git config core.hooksPath .githooks
```

That makes every commit run `scripts/check_secrets.py` first and refuse if a
key or any editorial content would go up. The repo is public; `.env` holds
live API keys and the database password.

## Layout

```
app/
  api.py          FastAPI routes
  judge.py        scoring — reads only, never edits
  editor.py       applies accepted findings — scores nothing
  doctor.py       the clinician verification sheet
  images.py       cover and in-article image briefs
  batch.py        batches, the background loop, the shadow judge
  engines.py      one call shape, two providers — the model name decides
  auth.py         Google sign-in via Supabase
  store.py        persistence; every run pins model, effort, ruleset version
  export.py       DOCX, PDF and printable HTML
  schema.sql      first-run schema
  migrate.sql     additive changes since — idempotent
  prompts/        judge, editor, doctor, images
corpus/
  source/         the Bible, extracted verbatim
  compiled/       the operative ruleset the model actually reads
web/              the app — one page, no build step
scripts/          catalogue scraper, DB setup, content sync, shadow report, checks
```

## The compiled ruleset

`corpus/compiled/parentveda-ruleset.md` is the Editorial Handbook, Publishing
Bible, Design Playbook and review framework reduced to operative rules, plus
everything learned since from real reviews: how to read as the parent before
judging as the editor, the corrections made most often, what separates an 8.8
from a 9.2, the bridges drafts most often lack, and what a clinician actually
needs to validate. It is sent with every call and cached.

**The ruleset and prompts are not in this repo.** They live in the Postgres
`content` table, which is what the app reads; the local files under
`corpus/compiled/` and `app/prompts/` are gitignored working copies. To change
how articles are judged: edit the file, run `python scripts/sync_content.py`,
then press **Reload ruleset** on the Experts screen — or wait for the next
deploy. Every run records which version produced it, so an old score stays
explicable.

## Deployment

Railway builds from `main` on every push; environment variables live there,
not in the repo. Database migrations are applied with
`python scripts/migrate.py` against `DATABASE_URL` — `app/migrate.sql` is
idempotent, so re-running it is safe. `/health` reports the engine, the models,
the loaded ruleset versions and the batch state.
