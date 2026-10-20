-- Custom Catalog storage for 3DRBXMT.
-- Run ONCE in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
--
-- Only IDs, item types, names, the owner's Roblox user ID + username, and the
-- visibility are stored. Never tokens, never 3D files.

create table if not exists public.custom_catalogs (
  code        text primary key
              check (code ~ '^[A-Z2-9]{4}-[A-Z2-9]{4}$'),                 -- e.g. K7M2-Q9XA
  owner_id    text not null
              check (owner_id ~ '^[0-9]{1,20}$'),                          -- Roblox user ID
  owner_name  text not null default '',                                    -- Roblox username, shown on public catalogs
  name        text not null
              check (char_length(name) between 1 and 40),
  visibility  text not null default 'private'
              check (visibility in ('public', 'private')),
  items       jsonb not null
              check (jsonb_typeof(items) = 'array'
                     and jsonb_array_length(items) between 1 and 50),      -- [{"id":123,"itemType":"Asset","name":"..."}]
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- "My catalogs" lookups (newest first)
create index if not exists custom_catalogs_owner_idx
  on public.custom_catalogs (owner_id, updated_at desc);

-- Lock the table down. Row Level Security ON with NO policies means the public
-- (anon) and logged-in (authenticated) Supabase keys can read and write nothing.
-- Only the backend can touch it, using the service_role key, which bypasses RLS.
alter table public.custom_catalogs enable row level security;
revoke all on public.custom_catalogs from anon, authenticated;

-- Make the API pick up the new table right away.
notify pgrst, 'reload schema';
