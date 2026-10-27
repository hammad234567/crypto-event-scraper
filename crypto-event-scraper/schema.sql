-- Supabase SQL Editor mein ek baar chalayen

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  exchange_name text not null,
  event_name text not null,
  event_url text,
  event_hash text unique not null,
  notified boolean default false,
  created_at timestamptz default now()
);

alter table events enable row level security;
drop policy if exists "public read events" on events;
create policy "public read events" on events for select using (true);

-- Notification band hai, is liye database trigger ki zaroorat nahi.
drop trigger if exists trg_new_event on events;
