"""Load the Nostos pipeline outputs into Tiger (TimescaleDB), schema nostos_wx.

Idempotent: applies sql/nostos_wx_schema.sql (IF NOT EXISTS everywhere), then upserts
every table with INSERT ... ON CONFLICT DO UPDATE, so it can be rerun safely.
Touches nothing outside nostos_wx. DATABASE_URL comes from .env.

Run after blend.py:  python load_to_db.py
"""
import os
import re
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

import blend
import config
from score import naive_baseline

SCHEMA_SQL = Path(__file__).parent / "sql" / "nostos_wx_schema.sql"
VIEWS_SQL = Path(__file__).parent / "sql" / "nostos_wx_views.sql"


def connect(**kw) -> psycopg.Connection:
    load_dotenv(Path(__file__).parent / ".env")
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15, **kw)


def apply_schema(conn):
    sql = SCHEMA_SQL.read_text() + ";\n" + VIEWS_SQL.read_text()
    code = re.sub(r"--[^\n]*", "", sql)
    assert not re.search(r"\b(ALTER|DROP|TRUNCATE|DELETE)\b", code, re.I), "schema file must be additive"
    for stmt in filter(str.strip, code.split(";")):
        assert "nostos_wx" in stmt, f"statement outside nostos_wx: {stmt[:80]}"
        conn.execute(stmt)


def day_start(climate_date: pd.Series, offset_h: pd.Series) -> pd.Series:
    return (pd.to_datetime(climate_date).dt.tz_localize("UTC")
            - pd.to_timedelta(offset_h, unit="h"))


def issued_at(start: pd.Series, lead: pd.Series) -> pd.Series:
    # Same rule as daily_high.py: 23:00 local standard time on climate_date - lead
    return start - pd.to_timedelta(lead - 1, unit="D") - pd.Timedelta(hours=1)


def build_frames():
    locs = config.load_locations()
    offsets = locs.set_index("location")["std_utc_offset_hours"]
    fc, obs = blend.load()
    end = pd.Timestamp(config.END_DATE)

    obs_rows = obs.assign(day_start=day_start(obs["climate_date"], obs["location"].map(offsets)))

    role = {m: "input" for m in config.MODELS} | {m: "benchmark" for m in config.BENCHMARK_MODELS}
    models = fc.assign(role=fc["model"].map(role))

    nostos = pd.read_parquet(config.DATA_DIR / "nostos_daily_high.parquet")
    nostos = nostos.assign(model="nostos", role="nostos",
                           climate_date=pd.to_datetime(nostos["climate_date"]))

    nbm_corr = blend.benchmark_corrected(fc, obs).assign(role="benchmark")

    naive = naive_baseline(obs).assign(role="baseline")
    naive = naive[naive["climate_date"] <= end]
    naive["issued_at"] = issued_at(day_start(naive["climate_date"], naive["location"].map(offsets)),
                                   naive["lead"])

    cols = ["location", "model", "role", "lead", "climate_date", "issued_at", "high_f"]
    fcs = pd.concat([d[cols] for d in (models, nostos, nbm_corr, naive)], ignore_index=True)
    fcs = fcs.dropna(subset=["high_f"])
    fcs["day_start"] = day_start(fcs["climate_date"], fcs["location"].map(offsets))
    fcs = fcs.merge(obs[["location", "climate_date", "high_f"]].rename(columns={"high_f": "observed_f"}),
                    on=["location", "climate_date"], how="left")
    assert fcs["role"].notna().all()
    assert (fcs["issued_at"] < fcs["day_start"]).all(), "a forecast issued after its day began"
    assert not fcs.duplicated(["location", "model", "lead", "climate_date"]).any()

    scores = pd.concat([
        pd.read_csv(config.DATA_DIR / "scores.csv").assign(split="full_period"),
        pd.read_csv(config.DATA_DIR / "val_scores.csv").assign(split="validation"),
        pd.read_csv(config.DATA_DIR / "test_scores.csv").assign(split="test"),
    ], ignore_index=True)
    scores["lead"] = scores["lead"].replace("all", 0).astype(int)
    if "vs_nbm_f" not in scores:
        scores["vs_nbm_f"] = None
    return locs, obs_rows, fcs, scores


def upsert(conn, table: str, df: pd.DataFrame, cols: list[str], key: list[str]):
    """COPY into a temp table, then INSERT ... ON CONFLICT DO UPDATE (only rows that changed)."""
    tmp = f"tmp_{table.split('.')[-1]}"
    conn.execute(f"CREATE TEMP TABLE {tmp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    with conn.cursor().copy(f"COPY {tmp} ({', '.join(cols)}) FROM STDIN") as copy:
        for row in df[cols].astype(object).where(df[cols].notna(), None).itertuples(index=False):
            copy.write_row(row)
    updates = [c for c in cols if c not in key]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
    changed = " OR ".join(f"{table}.{c} IS DISTINCT FROM EXCLUDED.{c}" for c in updates)
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM {tmp} "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {set_clause} WHERE {changed}")
    print(f"  {table}: {len(df):,} rows staged, {cur.rowcount:,} inserted/updated")


EXAMPLE_JOIN = """
-- Test-window MAE by model and lead, recomputed from scratch by joining forecasts to official highs
SELECT f.lead_days, f.model,
       count(*)                                  AS days,
       round(avg(abs(f.high_f - o.high_f))::numeric, 2) AS mae_f,
       round(avg(f.high_f - o.high_f)::numeric, 2)      AS bias_f
FROM nostos_wx.daily_high_forecasts f
JOIN nostos_wx.daily_high_obs o USING (location, climate_date)
WHERE f.climate_date BETWEEN %(start)s AND %(end)s
  AND f.model IN ('nostos', 'nbm_corrected', 'ncep_nbm_conus', 'gfs_seamless', 'icon_seamless')
GROUP BY 1, 2
ORDER BY 1, mae_f
"""

EXAMPLE_CAGG = """
-- Same leaderboard from the continuous aggregate, monthly via time_bucket
SELECT time_bucket(INTERVAL '1 month', day) AS month, model,
       round((sum(sum_abs_err_f) / sum(n))::numeric, 2) AS mae_f,
       round((sum(sum_err_f) / sum(n))::numeric, 2)     AS bias_f
FROM nostos_wx.leaderboard_daily
WHERE lead_days = 1 AND model IN ('nostos', 'nbm_corrected', 'ncep_nbm_conus')
  AND day >= %(start)s
GROUP BY 1, 2
ORDER BY 1, mae_f
"""


def main():
    locs, obs, fcs, scores = build_frames()
    with connect(autocommit=True) as conn:
        print("applying schema nostos_wx")
        apply_schema(conn)
    with connect() as conn, conn.transaction():
        print("upserting")
        upsert(conn, "nostos_wx.locations",
               locs.rename(columns={"lat": "latitude", "lon": "longitude"}),
               ["location", "station_id", "latitude", "longitude", "std_utc_offset_hours", "kalshi_series"],
               ["location"])
        upsert(conn, "nostos_wx.daily_high_obs", obs,
               ["location", "climate_date", "day_start", "station_id", "high_f", "published_at"],
               ["location", "climate_date"])
        upsert(conn, "nostos_wx.daily_high_forecasts", fcs.rename(columns={"lead": "lead_days"}),
               ["location", "model", "role", "lead_days", "climate_date", "day_start", "issued_at",
                "high_f", "observed_f"],
               ["location", "model", "lead_days", "climate_date"])
        upsert(conn, "nostos_wx.scores", scores.rename(columns={"lead": "lead_days"}),
               ["split", "lead_days", "model", "days", "mae_f", "bias_f", "within_2f_pct", "vs_nbm_f"],
               ["split", "model", "lead_days"])
    with connect(autocommit=True) as conn:
        print("refreshing continuous aggregate nostos_wx.leaderboard_daily")
        conn.execute("CALL refresh_continuous_aggregate('nostos_wx.leaderboard_daily', NULL, NULL)")

        _, test_start, end = blend.splits()
        params = {"start": test_start.date(), "end": end.date()}
        print("\n" + EXAMPLE_JOIN.strip().splitlines()[0])
        print(pd.DataFrame(conn.execute(EXAMPLE_JOIN, params).fetchall(),
                           columns=["lead", "model", "days", "mae_f", "bias_f"]).to_string(index=False))
        print("\n" + EXAMPLE_CAGG.strip().splitlines()[0])
        print(pd.DataFrame(conn.execute(EXAMPLE_CAGG, params).fetchall(),
                           columns=["month", "model", "mae_f", "bias_f"]).to_string(index=False))


if __name__ == "__main__":
    main()
