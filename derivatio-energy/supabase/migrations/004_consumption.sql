create table consumption_data (
  id uuid primary key default gen_random_uuid(),
  property_id uuid references properties(id) on delete cascade,
  timestamp timestamptz not null,
  kwh numeric not null,
  kw_peak numeric,
  source text default 'metry',
  created_at timestamptz default now(),
  unique(property_id, timestamp)
);

create index idx_consumption_property_time on consumption_data(property_id, timestamp);

alter table consumption_data enable row level security;
create policy "users can read own consumption" on consumption_data for select
  using (property_id in (
    select id from properties where organization_id in (
      select organization_id from profiles where id = auth.uid()
    )
  ));

create policy "users can insert consumption" on consumption_data for insert
  with check (property_id in (
    select id from properties where organization_id in (
      select organization_id from profiles where id = auth.uid()
    )
  ));