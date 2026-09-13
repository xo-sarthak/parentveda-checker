-- Additive, idempotent changes since schema.sql. Run: python scripts/migrate.py
alter table versions add column if not exists note         text;   -- what Apply did: "8 swaps, 2 rewrites"
alter table versions add column if not exists created_by   text;
alter table versions add column if not exists image_briefs jsonb;  -- cover + in-article visual prompts
alter table versions add column if not exists cost_usd     numeric(8,4) default 0;  -- rewrite cost, if a model ran
