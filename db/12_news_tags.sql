-- News tags: feed sections (markets, energy, ...) plus keyword topics (oil, power-grid, ...).
-- Written by news.py; an article seen in several feeds accumulates every section. Safe to re-run.

ALTER TABLE rss_articles ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS rss_articles_tags_idx ON rss_articles USING GIN (tags);
