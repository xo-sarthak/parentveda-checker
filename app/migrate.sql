-- Additive, idempotent changes since schema.sql. Run: python scripts/migrate.py
alter table versions add column if not exists note         text;   -- what Apply did: "8 swaps, 2 rewrites"
alter table versions add column if not exists created_by   text;
alter table versions add column if not exists image_briefs jsonb;  -- cover + in-article visual prompts
alter table versions add column if not exists cost_usd     numeric(8,4) default 0;  -- rewrite cost, if a model ran

-- Batched reviews (ChatGPT only). An article waits in queue_items until the
-- poller submits it; results are attached as ordinary runs.
alter table runs add column if not exists batch boolean not null default false;

create table if not exists batches (
  id            uuid primary key default uuid_generate_v4(),
  openai_id     text unique,
  status        text not null default 'submitted',   -- submitted | completed | expired | failed
  input_file_id text,
  output_file_id text,
  error_file_id text,
  n_items       int not null default 0,
  error         text,
  created_at    timestamptz not null default now(),
  completed_at  timestamptz
);

create table if not exists queue_items (
  id            uuid primary key default uuid_generate_v4(),
  article_id    uuid not null references articles(id) on delete cascade,
  version_id    uuid not null references versions(id) on delete cascade,
  model         text not null,
  effort        text not null default 'high',
  status        text not null default 'queued',      -- queued | submitted | done | failed
  batch_id      uuid references batches(id),
  attempts      int not null default 0,
  error         text,
  created_by    text,
  created_at    timestamptz not null default now(),
  submitted_at  timestamptz,
  completed_at  timestamptz
);
create index if not exists queue_items_status_idx on queue_items (status);
create index if not exists queue_items_article_idx on queue_items (article_id);

-- Shadow judge: a second, cheaper model reviews every queued article in the
-- same batch. Stored here, never shown to interns; scripts/shadow_report.py
-- compares it with the real run to decide whether the cheap model is enough.
create table if not exists shadow_reviews (
  id          uuid primary key default uuid_generate_v4(),
  version_id  uuid not null references versions(id) on delete cascade,
  model       text not null,
  overall     numeric(4,2),
  verdict     text,
  review      jsonb not null,             -- the full review dict, findings included
  cost_usd    numeric(8,4) not null default 0,
  error       text,
  created_at  timestamptz not null default now()
);
create index if not exists shadow_reviews_version_idx on shadow_reviews (version_id);

-- OpenAI allows one model per batch: the shadow judge gets its own.
alter table batches add column if not exists kind text not null default 'main';   -- main | shadow
alter table queue_items add column if not exists shadow_batch_id uuid references batches(id);
