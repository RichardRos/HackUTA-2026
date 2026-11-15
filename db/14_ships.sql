-- ============================================================================
-- nostos schema: live ships from AIS (aisstream.io). Safe to re-run:
--     python db/run_sql.py db/14_ships.sql
--
--   * vessels: one row per MMSI, the latest static data heard (name, type, destination...).
--   * ship_positions (hypertable): one row per vessel per ingest run (ingest/update.py --only ships
--     listens for --ships-seconds and stores each vessel's latest fix). Kept 90 days.
-- ============================================================================

CREATE TABLE IF NOT EXISTS vessels (
    mmsi         BIGINT PRIMARY KEY,
    name         TEXT,
    imo          BIGINT,
    callsign     TEXT,
    flag         TEXT,
    type_code    SMALLINT,
    category     TEXT,               -- cargo, tanker, passenger, special, ... (ships.ship_type)
    hazard       TEXT,               -- e.g. 'hazardous category A' for cargo/tankers carrying DG
    destination  TEXT,               -- as typed by the crew, free text
    eta          TEXT,               -- 'MM-DD HH:MM UTC', no year in AIS
    length_m     SMALLINT,
    beam_m       SMALLINT,
    draught_m    REAL,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vessels_category_idx ON vessels (category);

CREATE TABLE IF NOT EXISTS ship_positions (
    time       TIMESTAMPTZ      NOT NULL,   -- when the ingest run heard this fix
    mmsi       BIGINT           NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    speed_kn   REAL,
    course     REAL,
    heading    SMALLINT,
    status     TEXT,
    PRIMARY KEY (mmsi, time)
);
SELECT create_hypertable('ship_positions', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);
SELECT add_retention_policy('ship_positions', INTERVAL '90 days', if_not_exists => TRUE);
