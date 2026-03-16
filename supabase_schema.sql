-- Run this in your Supabase SQL editor (once)
-- Dashboard: app.supabase.com → your project → SQL Editor → New query

-- Users table (UG Drive accounts)
create table if not exists public.users (
  id            uuid default gen_random_uuid() primary key,
  email         text unique not null,
  password_hash text not null,
  name          text default '',
  avatar        text default '',
  created_at    timestamptz default now()
);

-- Google Drive accounts (tokens stored here — survives Render restarts)
create table if not exists public.google_accounts (
  id            bigserial primary key,
  user_id       uuid references public.users(id) on delete cascade not null,
  email         text not null,
  name          text default '',
  avatar        text default '',
  token_b64     text not null,
  total_bytes   bigint default 0,
  used_bytes    bigint default 0,
  synced_at     timestamptz,
  created_at    timestamptz default now(),
  unique(user_id, email)
);

-- File cache (synced from Google Drive — rebuilds on demand)
create table if not exists public.file_cache (
  gid           text not null,
  account_id    bigint references public.google_accounts(id) on delete cascade not null,
  user_id       uuid references public.users(id) on delete cascade not null,
  name          text default '',
  mime          text default '',
  size          bigint default 0,
  parent_gid    text,
  created_at    text,
  modified_at   text,
  trashed       boolean default false,
  view_link     text default '',
  primary key(gid, account_id)
);

create index if not exists idx_fc_user    on public.file_cache(user_id);
create index if not exists idx_fc_account on public.file_cache(account_id);
create index if not exists idx_fc_trashed on public.file_cache(trashed);
create index if not exists idx_fc_name    on public.file_cache(name);

-- Disable RLS (we handle auth ourselves server-side)
alter table public.users         disable row level security;
alter table public.google_accounts disable row level security;
alter table public.file_cache    disable row level security;

-- Reset tokens table (for forgot password flow)
-- Run this in Supabase SQL Editor if you haven't already
create table if not exists public.reset_tokens (
  id          bigserial primary key,
  user_id     uuid references public.users(id) on delete cascade not null,
  token       text unique not null,
  expires_at  timestamptz not null,
  used        boolean default false,
  created_at  timestamptz default now()
);

create index if not exists idx_rt_token   on public.reset_tokens(token);
create index if not exists idx_rt_user    on public.reset_tokens(user_id);
create index if not exists idx_rt_expires on public.reset_tokens(expires_at);

alter table public.reset_tokens disable row level security;
