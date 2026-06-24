INSERT INTO taskbound.signing_keys VALUES ('dev', 'dev-secret-change-me');

INSERT INTO app_data.departments VALUES
  ('dep_fin', 'company_a', 'Finance', 'user:alice'),
  ('dep_sales', 'company_a', 'Sales', 'user:alice'),
  ('dep_eng', 'company_a', 'Engineering', 'user:alice'),
  ('dep_fin_b', 'company_b', 'Finance', 'user:diana');

INSERT INTO app_data.employees VALUES
  ('emp_001', 'company_a', 'dep_fin', 'Alice Zhang', 'M2', '13800000001', '6222-0001', 48000),
  ('emp_002', 'company_a', 'dep_fin', 'Ben Liu', 'L4', '13800000002', '6222-0002', 28000),
  ('emp_003', 'company_a', 'dep_sales', 'Cindy Wang', 'L5', '13800000003', '6222-0003', 32000),
  ('emp_004', 'company_a', 'dep_sales', 'David Chen', 'L3', '13800000004', '6222-0004', 19000),
  ('emp_005', 'company_a', 'dep_eng', 'Eva Li', 'L5', '13800000005', '6222-0005', 35000),
  ('emp_006', 'company_b', 'dep_fin_b', 'Frank Wu', 'M1', '13900000006', '6333-0006', 45000);

INSERT INTO app_data.expenses VALUES
  ('exp_001', 'company_a', 'emp_001', 'dep_fin', '2026-06', 'flight', 'Air China', 'Beijing', 1880, '2026-06-03T10:00:00Z', 'payable'),
  ('exp_002', 'company_a', 'emp_001', 'dep_fin', '2026-06', 'hotel', 'Hilton', 'Shanghai', 2360, '2026-06-04T10:00:00Z', 'submitted'),
  ('exp_003', 'company_a', 'emp_002', 'dep_fin', '2026-06', 'taxi', 'Didi', 'Shanghai', 180, '2026-06-05T10:00:00Z', 'payable'),
  ('exp_004', 'company_a', 'emp_003', 'dep_sales', '2026-06', 'flight', 'China Eastern', 'Shenzhen', 2100, '2026-06-06T10:00:00Z', 'payable'),
  ('exp_005', 'company_a', 'emp_003', 'dep_sales', '2026-06', 'meal', 'Client Dinner', 'Shenzhen', 3680, '2026-06-06T20:00:00Z', 'payable'),
  ('exp_006', 'company_a', 'emp_004', 'dep_sales', '2026-06', 'hotel', 'Marriott', 'Guangzhou', 1680, '2026-06-08T10:00:00Z', 'payable'),
  ('exp_007', 'company_a', 'emp_005', 'dep_eng', '2026-06', 'train', 'CRH', 'Hangzhou', 560, '2026-06-09T10:00:00Z', 'payable'),
  ('exp_008', 'company_a', 'emp_005', 'dep_eng', '2026-06', 'equipment', 'Apple Store', 'Hangzhou', 12999, '2026-06-10T10:00:00Z', 'submitted'),
  ('exp_009', 'company_a', 'emp_003', 'dep_sales', '2026-05', 'hotel', 'Hyatt', 'Chengdu', 1500, '2026-05-12T10:00:00Z', 'approved'),
  ('exp_010', 'company_b', 'emp_006', 'dep_fin_b', '2026-06', 'flight', 'Air China', 'Beijing', 1999, '2026-06-03T10:00:00Z', 'approved');
