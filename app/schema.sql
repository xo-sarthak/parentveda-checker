-- ParentVeda Article Checker — schema v1
-- Every score stays explicable: runs pin the model, effort and corpus version.

create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------- articles

create type article_status as enum (
  'draft', 'reviewed', 'exported', 'sent_for_verification', 'verified', 'archived'
);

create table articles (
  id            uuid primary key default uuid_generate_v4(),
  title         text not null,
  topic         text,
  article_type  text,
  author        text,                       -- intern / writer name
  status        article_status not null default 'draft',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index articles_status_idx  on articles (status);
create index articles_created_idx on articles (created_at desc);
-- full-text search over title + topic, for "find that article from last month"
create index articles_search_idx  on articles
  using gin (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(topic,'')));

-- ---------------------------------------------------------------- versions

create table versions (
  id          uuid primary key default uuid_generate_v4(),
  article_id  uuid not null references articles(id) on delete cascade,
  version_no  int  not null,               -- 1, 2, 3...
  body        text not null,
  word_count  int  not null,
  source      text not null default 'upload',   -- upload | rewrite
  created_at  timestamptz not null default now(),
  unique (article_id, version_no)
);

create index versions_article_idx on versions (article_id, version_no);

-- ---------------------------------------------------------------- runs

create table runs (
  id              uuid primary key default uuid_generate_v4(),
  version_id      uuid not null references versions(id) on delete cascade,
  kind            text not null,            -- review | verify
  model           text not null,
  effort          text not null,
  ruleset_version text not null,            -- which compiled Bible produced this
  overall         numeric(4,2) not null,
  verdict         text not null,
  blockers        jsonb not null default '[]',
  article_level   jsonb not null default '{}',
  expert_review   jsonb not null default '{}',
  tokens_in       int, tokens_out int,
  cache_write     int, cache_read int,
  cost_usd        numeric(8,4),
  created_at      timestamptz not null default now()
);

create index runs_version_idx on runs (version_id, created_at desc);

-- ---------------------------------------------------------------- scores

create table scores (
  id            uuid primary key default uuid_generate_v4(),
  run_id        uuid not null references runs(id) on delete cascade,
  parameter     text not null,
  score         numeric(3,1) not null,
  justification text,
  unique (run_id, parameter)
);

-- ---------------------------------------------------------------- feedback

create type feedback_tier    as enum ('must', 'should', 'polish');
create type feedback_kind    as enum ('line', 'structural');
create type decision_outcome as enum ('pending', 'accepted', 'rejected', 'edited');

create table feedback (
  id         uuid primary key default uuid_generate_v4(),
  run_id     uuid not null references runs(id) on delete cascade,
  tier       feedback_tier not null,
  kind       feedback_kind not null,
  parameter  text not null,
  summary    text not null,
  quote      text,                          -- null for structural items
  proposed   text not null,
  rationale  text,
  position   int,                           -- display order
  created_at timestamptz not null default now()
);

create index feedback_run_idx on feedback (run_id, tier);

-- Decisions are the calibration log: where Bhabhi disagrees with the tool.
create table decisions (
  id          uuid primary key default uuid_generate_v4(),
  feedback_id uuid not null references feedback(id) on delete cascade,
  outcome     decision_outcome not null default 'pending',
  edited_text text,                         -- if she reworded the proposal
  decided_by  text,
  decided_at  timestamptz,
  auto        boolean not null default false,  -- true if "Choose for me"
  unique (feedback_id)
);

create index decisions_outcome_idx on decisions (outcome);

-- ---------------------------------------------------------------- verification

create table verification (
  id          uuid primary key default uuid_generate_v4(),
  article_id  uuid not null references articles(id) on delete cascade,
  specialty   text,                         -- paediatrician, obstetrician, lawyer...
  doctor_name text,
  sheet_text  text,                         -- the generated doctor TLDR
  sent_at     timestamptz,
  verified_at timestamptz,
  notes       text,
  created_at  timestamptz not null default now()
);

create index verification_article_idx on verification (article_id);

-- ---------------------------------------------------------------- published

-- The 24 live articles: duplication detection + internal-link targets.
create table published (
  id         uuid primary key default uuid_generate_v4(),
  slug       text unique not null,
  url        text not null,
  title      text not null,
  summary    text,
  body       text not null,
  word_count int not null,
  headings   jsonb not null default '[]',
  fetched_at timestamptz not null default now()
);

create index published_search_idx on published
  using gin (to_tsvector('english', title || ' ' || coalesce(summary,'') || ' ' || body));

-- ---------------------------------------------------------------- corpus

create table corpus_versions (
  id         uuid primary key default uuid_generate_v4(),
  version    text unique not null,          -- 'v1', 'v2'...
  ruleset    text not null,                 -- the compiled Bible, verbatim
  note       text,
  created_at timestamptz not null default now()
);
