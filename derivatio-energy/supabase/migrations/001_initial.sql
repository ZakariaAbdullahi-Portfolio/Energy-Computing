create extension if not exists "uuid-ossp";

create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  org_number text,
  created_at timestamptz default now()
);

create table profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  organization_id uuid references organizations(id),
  full_name text,
  role text default 'member',
  created_at timestamptz default now()
);

create table properties (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references organizations(id) on delete cascade,
  name text not null,
  address text,
  postal_code text,
  grid_operator text not null, -- vattenfall | ellevio | eon | goteborg_energi
  grid_area text not null,     -- SE1 | SE2 | SE3 | SE4
  subscription_kw numeric not null,
  metry_meter_id text,
  created_at timestamptz default now()
);

create table fleets (
  id uuid primary key default gen_random_uuid(),
  property_id uuid references properties(id) on delete cascade,
  name text not null,
  vehicle_count int default 1,
  charger_kw numeric default 22,
  avg_arrival_hour int default 17,
  avg_departure_hour int default 7,
  avg_soc_on_arrival numeric default 0.20,
  battery_kwh numeric default 77,
  created_at timestamptz default now()
);

alter table organizations enable row level security;
alter table profiles enable row level security;
alter table properties enable row level security;
alter table fleets enable row level security;

create policy "users can read own org" on organizations for select
  using (id in (select organization_id from profiles where id = auth.uid()));

create policy "users can read own profile" on profiles for select
  using (id = auth.uid());

create policy "users can read own properties" on properties for select
  using (organization_id in (select organization_id from profiles where id = auth.uid()));

create policy "users can insert properties" on properties for insert
  with check (organization_id in (select organization_id from profiles where id = auth.uid()));

create policy "users can read fleets" on fleets for select
  using (property_id in (
    select id from properties where organization_id in (
      select organization_id from profiles where id = auth.uid()
    )
  ));