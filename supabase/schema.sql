-- Base tables. Copied unchanged from the original project (Phase 1).
-- Run order: this file, then supabase/migrations/* by timestamp, then seed.sql.

create table if not exists public.api_keys (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default gen_random_uuid(),
  name text not null check (length(trim(name)) between 1 and 100),
  key_hash text not null unique check (key_hash ~ '^[0-9a-f]{64}$'),
  key_prefix text not null,
  scopes text[] not null check (
    cardinality(scopes) > 0
    and scopes <@ array['read', 'write']::text[]
  ),
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);

create table if not exists public.parcels (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null,
  tracking_id text not null unique check (
    length(trim(tracking_id)) between 3 and 80
  ),
  status text not null check (
    status in (
      'created',
      'picked_up',
      'in_transit',
      'out_for_delivery',
      'delivered',
      'delayed',
      'failed_delivery'
    )
  ),
  eta timestamptz,
  destination text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.parcel_events (
  id uuid primary key default gen_random_uuid(),
  parcel_id uuid not null references public.parcels(id) on delete cascade,
  status text not null check (
    status in (
      'created',
      'picked_up',
      'in_transit',
      'out_for_delivery',
      'delivered',
      'delayed',
      'failed_delivery'
    )
  ),
  description text not null check (length(trim(description)) between 1 and 500),
  location text,
  occurred_at timestamptz not null default now()
);

create table if not exists public.webhooks (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null,
  url text not null check (url like 'https://%'),
  secret text not null,
  events text[] not null default array['parcel.status_updated']::text[],
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.webhook_deliveries (
  id uuid primary key default gen_random_uuid(),
  webhook_id uuid not null references public.webhooks(id) on delete cascade,
  parcel_id uuid references public.parcels(id) on delete set null,
  event_type text not null,
  payload jsonb not null,
  attempt_number smallint not null check (attempt_number between 1 and 3),
  response_status integer,
  response_body text,
  error text,
  attempted_at timestamptz not null default now()
);

create table if not exists public.rate_limits (
  api_key_id uuid not null references public.api_keys(id) on delete cascade,
  window_start timestamptz not null,
  request_count integer not null default 0 check (request_count >= 0),
  primary key (api_key_id, window_start)
);

create index if not exists parcels_owner_id_idx
  on public.parcels(owner_id);
create index if not exists parcel_events_parcel_occurred_idx
  on public.parcel_events(parcel_id, occurred_at);
create index if not exists webhooks_owner_active_idx
  on public.webhooks(owner_id, active);
create index if not exists webhook_deliveries_webhook_attempted_idx
  on public.webhook_deliveries(webhook_id, attempted_at desc);
create index if not exists rate_limits_window_start_idx
  on public.rate_limits(window_start);

alter table public.api_keys enable row level security;
alter table public.parcels enable row level security;
alter table public.parcel_events enable row level security;
alter table public.webhooks enable row level security;
alter table public.webhook_deliveries enable row level security;
alter table public.rate_limits enable row level security;

revoke all on table
  public.api_keys,
  public.parcels,
  public.parcel_events,
  public.webhooks,
  public.webhook_deliveries,
  public.rate_limits
from anon, authenticated;

grant all on table
  public.api_keys,
  public.parcels,
  public.parcel_events,
  public.webhooks,
  public.webhook_deliveries,
  public.rate_limits
to service_role;
