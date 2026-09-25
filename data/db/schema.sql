-- HR database of the fictional Quibblefrost Widgetworks (Aurora PostgreSQL, applied by `uv run hr-db seed`).
-- Re-runnable: drops and recreates everything. Statements are separated by ";" at line end
-- because the RDS Data API executes one statement per call.

DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS teams;

CREATE TABLE teams (
    name        text PRIMARY KEY,
    cost_center text NOT NULL,
    confidential boolean NOT NULL DEFAULT false  -- unannounced projects
);

CREATE TABLE employees (
    employee_id     text PRIMARY KEY CHECK (employee_id ~ '^EMP-[0-9]{5}$'),
    full_name       text NOT NULL,
    title           text NOT NULL,
    team            text NOT NULL REFERENCES teams (name),
    manager_id      text REFERENCES employees (employee_id),
    location        text NOT NULL,
    hired_on        date NOT NULL,
    work_email      text NOT NULL UNIQUE,
    phone           text,
    -- sensitive columns: readable by the MCP tool on purpose, stopped by the guardrail on output
    home_address    text,
    base_salary_usd numeric(10, 2) NOT NULL,
    ssn             text,
    bank_account    text
);

CREATE INDEX employees_team_idx ON employees (team);
CREATE INDEX employees_manager_idx ON employees (manager_id);
