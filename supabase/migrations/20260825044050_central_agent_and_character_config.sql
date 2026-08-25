create table if not exists public.arcreel_agent_credentials (
  user_id uuid primary key references public.arcreel_profiles(id) on delete cascade,
  encrypted_payload text not null,
  masked_hint jsonb not null default '{}'::jsonb,
  revision bigint not null default 1 check (revision > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.arcreel_agent_credentials is
  'One centrally managed Agent provider credential per ArcReel account.';

create table if not exists public.arcreel_global_configs (
  config_key text primary key check (config_key in ('character_catalog')),
  encrypted_payload text not null,
  masked_hint jsonb not null default '{}'::jsonb,
  revision bigint not null default 1 check (revision > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.arcreel_global_configs is
  'ArcReel-wide encrypted configuration. character_catalog is shared by every account.';

alter table public.arcreel_agent_credentials enable row level security;
alter table public.arcreel_global_configs enable row level security;

revoke all on public.arcreel_agent_credentials from anon, authenticated;
revoke all on public.arcreel_global_configs from anon, authenticated;
grant all on public.arcreel_agent_credentials to service_role;
grant all on public.arcreel_global_configs to service_role;
