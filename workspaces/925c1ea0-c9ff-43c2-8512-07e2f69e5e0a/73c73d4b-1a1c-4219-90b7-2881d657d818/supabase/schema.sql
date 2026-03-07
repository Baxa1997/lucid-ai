-- ============================================================
-- ShopsReady — Supabase Schema
-- Run this in: Supabase Dashboard → SQL Editor → Run
-- ============================================================

-- ─── 1. PROFILES ───────────────────────────────────────────
-- Stores extra user data (plan, limits) linked to auth.users
create table if not exists public.profiles (
  id                     uuid primary key references auth.users on delete cascade,
  full_name              text,
  avatar_url             text,
  plan                   text not null default 'free', -- 'free' | 'pro'
  stripe_customer_id     text unique,
  stripe_subscription_id text unique,
  subscription_status    text default 'inactive',      -- 'active' | 'canceled' | 'past_due' | 'inactive'
  daily_generation_count int  not null default 0,
  daily_reset_date       date not null default current_date,
  created_at             timestamptz not null default now()
);

-- Public read/write only for the owner
alter table public.profiles enable row level security;

create policy "Users can view their own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update their own profile"
  on public.profiles for update
  using (auth.uid() = id);

-- ─── 2. AUTO-CREATE PROFILE ON SIGN UP ─────────────────────
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, full_name, avatar_url)
  values (
    new.id,
    new.raw_user_meta_data->>'full_name',
    new.raw_user_meta_data->>'avatar_url'
  );
  return new;
end;
$$;

-- Drop trigger if it already exists, then recreate
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();


-- ─── 3. PROJECTS ────────────────────────────────────────────
-- Stores every PDF processing result per user
create table if not exists public.projects (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles(id) on delete cascade,
  file_name     text not null,
  marketplace   text not null default 'shopify', -- 'shopify' | 'amazon'
  product_count int  not null default 0,
  status        text not null default 'done',    -- 'done' | 'failed'
  products      jsonb not null default '[]',     -- full UnifiedProduct[] array
  created_at    timestamptz not null default now()
);

-- Users can only see/delete their own projects
alter table public.projects enable row level security;

create policy "Users can view their own projects"
  on public.projects for select
  using (auth.uid() = user_id);

create policy "Users can insert their own projects"
  on public.projects for insert
  with check (auth.uid() = user_id);

create policy "Users can delete their own projects"
  on public.projects for delete
  using (auth.uid() = user_id);

-- Index for fast per-user history queries
create index if not exists projects_user_id_created_at_idx
  on public.projects (user_id, created_at desc);
