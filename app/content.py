"""Editorial content lives in the database, not the repo.

The ruleset and prompts are ParentVeda's method — they stay out of source
control. The app loads them from Postgres, falling back to local files during
development. Editing the standard is a database write, not a deploy.
"""
import os
from functools import lru_cache

from app import config

_FALLBACK = {
    "ruleset": config.ROOT / "corpus" / "compiled" / "parentveda-ruleset.md",
    "judge": config.PROMPTS / "judge.md",
    "editor": config.PROMPTS / "editor.md",
    "doctor": config.PROMPTS / "doctor.md",
}


def _from_db(name: str) -> str | None:
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                             row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("select body from content where name=%s and active", (name,))
            row = cur.fetchone()
            return row["body"] if row else None
    except Exception:
        return None


@lru_cache(maxsize=8)
def get(name: str) -> str:
    """Database first, local file second. Raises if neither has it."""
    body = _from_db(name)
    if body:
        return body

    path = _FALLBACK.get(name)
    if path and path.exists():
        return path.read_text(encoding="utf-8")

    raise RuntimeError(
        f"No editorial content named '{name}'. It is not in the database and "
        f"there is no local file at {path}. Run scripts/sync_content.py against "
        f"a machine that has the source files."
    )


@lru_cache(maxsize=1)
def catalogue() -> str:
    """Compact index of published articles, for duplication and link checks.
    Built from the database so a fresh deploy needs no local files."""
    if not os.environ.get("DATABASE_URL"):
        return ""
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                             row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("select title, url, summary, word_count, headings "
                        "from published order by slug")
            rows = cur.fetchall()
    except Exception:
        return ""

    out = []
    for a in rows:
        heads = [h.strip() for h in (a["headings"] or [])
                 if 3 < len(h.strip()) < 90][:9]
        out.append(f"### {a['title']}")
        out.append(f"url: {a['url']} · {a['word_count']} words")
        if a["summary"]:
            out.append(a["summary"])
        if heads:
            out.append("covers: " + " | ".join(heads))
        out.append("")
    return "\n".join(out)


def refresh() -> None:
    """Call after editing content so the next request picks it up."""
    get.cache_clear()
    catalogue.cache_clear()
