-- Database schema: run this once (e.g. in Railway SQL console or migration).

-- Example: users table aligned with schemas/input.py and schemas/db.py
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       VARCHAR(255) UNIQUE NOT NULL,
    name        VARCHAR(255) NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- RSVP table for frontend RSVP page
-- Unique on (name, email): same email can RSVP multiple names, same name can use multiple emails
CREATE TABLE IF NOT EXISTS rsvps (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    coming          BOOLEAN DEFAULT true,
    allergies       VARCHAR(500),
    transport_assist BOOLEAN DEFAULT false,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name, email)
);

-- Add more tables below, then add matching Pydantic models in schemas/input.py
-- and column names in schemas/db.py.
