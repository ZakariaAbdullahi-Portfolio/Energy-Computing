insert into grid_tariffs (
  operator, tariff_name, valid_from, valid_to,
  base_monthly_fee, capacity_fee_kw, peak_fee_kw,
  peak_hours_start, peak_hours_end, peak_months,
  peak_weekdays_only, peak_calc_method,
  energy_fee_peak, energy_fee_offpeak
) values (
  'eon', 'E.ON Företag', '2025-01-01', null,
  450, 30.0, 65.0,
  6, 22, '{11,12,1,2,3}',
  true, 'avg3',
  0.06, 0.02
);
```

---

### `requirements.txt`
```
fastapi==0.111.0
uvicorn[standard]==0.29.0
pydantic==2.7.0
pydantic-settings==2.2.1
supabase==2.4.6
httpx==0.27.0
pandas==2.2.2
numpy==1.26.4
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
```

---

### `.env.example`
```
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_KEY=your-service-key
SUPABASE_ANON_KEY=your-anon-key
ENTSOE_API_KEY=your-entsoe-key
METRY_API_KEY=your-metry-key
SMHI_BASE_URL=https://opendata-download-metobs.smhi.se
ENVIRONMENT=development