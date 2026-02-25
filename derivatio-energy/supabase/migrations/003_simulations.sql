create table simulations (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references organizations(id),
  property_id uuid references properties(id),
  created_by uuid references auth.users(id),
  status text default 'pending',
  period_start date not null,
  period_end date not null,
  grid_area text not null,
  input_params jsonb,
  result jsonb,
  cost_without_derivatio numeric,
  cost_with_derivatio numeric,
  savings_total numeric,
  savings_pct numeric,
  peak_kw_without numeric,
  peak_kw_with numeric,
  created_at timestamptz default now()
);

alter table simulations enable row level security;

create policy "users can read own simulations" on simulations for select
  using (organization_id in (select organization_id from profiles where id = auth.uid()));

create policy "users can insert simulations" on simulations for insert
  with check (organization_id in (select organization_id from profiles where id = auth.uid()));

create policy "users can update own simulations" on simulations for update
  using (organization_id in (select organization_id from profiles where id = auth.uid()));