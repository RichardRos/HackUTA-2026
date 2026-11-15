-- WSJ + FT RSS: latest raw feed XML and normalized articles.
-- Run with db/run_sql.py; safe to re-run. Articles upsert on a sha256 id.

CREATE TABLE IF NOT EXISTS rss_feeds (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     TEXT NOT NULL CHECK (source IN ('wsj', 'ft')),
    feed_url   TEXT NOT NULL,
    title      TEXT,
    raw_xml    TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    UNIQUE (source, feed_url)
);

CREATE TABLE IF NOT EXISTS rss_articles (
    id           TEXT PRIMARY KEY,
    feed_id      BIGINT NOT NULL REFERENCES rss_feeds (id) ON DELETE CASCADE,
    guid         TEXT,
    link         TEXT,
    title        TEXT NOT NULL,
    summary      TEXT,
    author       TEXT,
    published_at TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ,
    raw          JSONB NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS rss_articles_feed_published_idx
    ON rss_articles (feed_id, published_at DESC);
