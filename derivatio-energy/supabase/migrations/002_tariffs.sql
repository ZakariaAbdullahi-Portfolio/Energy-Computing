create table grid_tariffs (
  id uuid primary key default gen_random_uuid(),
  operator text not null,
  tariff_name text not null,
  valid_from date not null,
  valid_to date,
  base_monthly_fee numeric default 0,
  capacity_fee_kw numeric not null,      -- kr/kW basavgift (alltid)
  peak_fee_kw numeric not null,          -- kr/kW tillägg höglasttid
  peak_hours_start int not null,         -- 6 = 06:00
  peak_hours_end int not null,           -- 22 = 22:00
  peak_months int[] not null,            -- {11,12,1,2,3}
  peak_weekdays_only boolean default true,
  peak_calc_method text default 'single', -- single | avg3 | avg5
  energy_fee_peak numeric default 0,     -- kr/kWh höglast
  energy_fee_offpeak numeric default 0,  -- kr/kWh låglast
  created_at timestamptz default now()
);

alter table grid_tariffs enable row level security;
create policy "anyone can read tariffs" on grid_tariffs for select using (true);