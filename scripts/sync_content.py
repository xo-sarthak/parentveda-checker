"""Push the local editorial content into the database.

Run this from a machine that has the source files. The deployed app reads from
the database and never needs them on disk.

    python scripts/sync_content.py
"""
import hashlib
import io
import os
import sys

sys.path.insert(0, '.')
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import psycopg  # noqa: E402
from app import config  # noqa: E402

DDL = """
create table if not exists content (
  id         uuid primary key default uuid_generate_v4(),
  name       text not null,
  body       text not null,
  sha        text not null,
  active     boolean not null default true,
  note       text,
  updated_at timestamptz not null default now()
);
create unique index if not exists content_active_name
  on content (name) where active;
"""

SOURCES = {
    "ruleset": config.ROOT / "corpus" / "compiled" / "parentveda-ruleset.md",
    "judge": config.PROMPTS / "judge.md",
    "editor": config.PROMPTS / "editor.md",
    "doctor": config.PROMPTS / "doctor.md",
}


def main() -> None:
    missing = [n for n, p in SOURCES.items() if not p.exists()]
    if missing:
        print("missing local files:", ", ".join(missing))
        print("nothing synced — run this where the source files are.")
        return

    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.commit()

        for name, path in SOURCES.items():
            body = path.read_text(encoding="utf-8")
            sha = hashlib.sha256(body.encode()).hexdigest()[:12]

            with conn.cursor() as cur:
                cur.execute("select sha from content where name=%s and active", (name,))
                row = cur.fetchone()
                if row and row[0] == sha:
                    print(f"  = {name:<8} unchanged ({sha})")
                    continue
                # keep the old version, just retire it
                cur.execute("update content set active=false where name=%s", (name,))
                cur.execute(
                    "insert into content (name, body, sha, note) values (%s,%s,%s,%s)",
                    (name, body, sha, f"synced from {path.name}"))
                print(f"  {'~' if row else '+'} {name:<8} {'updated' if row else 'added'} "
                      f"({sha}, {len(body)} chars)")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("select name, sha, length(body) from content where active "
                        "order by name")
            print("\nactive content in the database:")
            for n, sha, ln in cur.fetchall():
                print(f"  {n:<8} {sha}  {ln:>6} chars")

        print()
        print("Written to the database. A running app still holds its old copy")
        print("in memory — open the Experts screen and press 'Reload ruleset',")
        print("or restart the service, before the change takes effect.")


if __name__ == "__main__":
    main()
