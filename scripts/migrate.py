"""Apply app/migrate.sql. Every statement is idempotent, so re-running is safe."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, store

sql = (config.ROOT / "app" / "migrate.sql").read_text(encoding="utf-8")
with store.connect() as conn, conn.cursor() as cur:
    cur.execute(sql)
    conn.commit()
print("migrated")
