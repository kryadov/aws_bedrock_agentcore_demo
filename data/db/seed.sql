-- Fictional demo data (applied by `uv run hr-db seed` after schema.sql).
-- SSNs are from the 987-65-432x range reserved by the SSA for advertising; bank details are test values.

INSERT INTO teams (name, cost_center, confidential) VALUES
    ('Executive', 'CC-100', false),
    ('Data Platform', 'CC-210', false),
    ('Customer Success', 'CC-320', false),
    ('Project Falcon', 'CC-990', true);

INSERT INTO employees (employee_id, full_name, title, team, manager_id, location, hired_on, work_email, phone,
                       home_address, base_salary_usd, ssn, bank_account) VALUES
    ('EMP-10318', 'Dana Whitfield', 'Chief Executive Officer', 'Executive', NULL, 'San Francisco', '2016-03-01',
     'dana.whitfield@quibblefrost.example', '+1 415 555 0101',
     '9 Sprocketwood Way, San Francisco, CA 94133', 305000, '987-65-4323', '110000000 / 000123456784'),
    ('EMP-10245', 'Ben Carter', 'Head of Data Platform', 'Data Platform', 'EMP-10318', 'Oakland', '2018-09-17',
     'ben.carter@quibblefrost.example', '+1 415 555 0177',
     '405 Widgetmaker Rd, Oakland, CA 94607', 214000, '987-65-4321', '110000000 / 000123456782'),
    ('EMP-10302', 'Chen Wei', 'Customer Success Manager', 'Customer Success', 'EMP-10318', 'New York', '2022-01-10',
     'chen.wei@quibblefrost.example', '+1 646 555 0110',
     '77 Quibble Court, New York, NY 10019', 128500, '987-65-4322', '110000000 / 000123456783'),
    ('EMP-10231', 'Alice Moreno', 'Senior Data Engineer', 'Data Platform', 'EMP-10245', 'San Francisco', '2020-06-01',
     'alice.moreno@quibblefrost.example', '+1 415 555 0142',
     '18 Frostquill Lane, San Francisco, CA 94112', 182000, '987-65-4320', '110000000 / 000123456781'),
    ('EMP-10420', 'Emil Novak', 'Software Engineer', 'Project Falcon', 'EMP-10245', 'Warsaw', '2025-02-03',
     'emil.novak@quibblefrost.example', '+48 22 555 0187',
     'ul. Zmyślona 7, 00-850 Warszawa', 96000, NULL, 'PL61109010140000071219812874');
