insert into grid_tariffs (
  operator, tariff_name, valid_from, valid_to,
  base_monthly_fee, capacity_fee_kw, peak_fee_kw,
  peak_hours_start, peak_hours_end, peak_months,
  peak_weekdays_only, peak_calc_method,
  energy_fee_peak, energy_fee_offpeak
) values (
  'ellevio', 'Ellevio Företag', '2025-01-01', null,
  500, 34.84, 59.32,
  6, 18, '{11,12,1,2,3}',
  true, 'avg3',
  0.05, 0.02
);