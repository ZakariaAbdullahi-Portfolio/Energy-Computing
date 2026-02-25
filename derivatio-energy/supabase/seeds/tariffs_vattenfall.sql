insert into grid_tariffs (
  operator, tariff_name, valid_from, valid_to,
  base_monthly_fee, capacity_fee_kw, peak_fee_kw,
  peak_hours_start, peak_hours_end, peak_months,
  peak_weekdays_only, peak_calc_method,
  energy_fee_peak, energy_fee_offpeak
) values (
  'vattenfall', 'N3T', '2025-01-01', null,
  400, 34.0, 71.0,
  6, 22, '{11,12,1,2,3}',
  true, 'single',
  0.06, 0.02
),
(
  'vattenfall', 'N3', '2025-01-01', null,
  300, 34.0, 86.0,
  6, 22, '{11,12,1,2,3}',
  true, 'single',
  0.07, 0.02
);