import unittest
from datetime import datetime, timezone

import news

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:wsj="http://dowjones.net/rss/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>WSJ.com: World News</title>
    <item>
      <title>AT&amp;T and the Gaza crossing</title>
      <link>https://www.wsj.com/articles/a-1?mod=rss_worldnews</link>
      <description><![CDATA[<p>Israel allowed displaced Gazans to cross &amp; return — again.</p>]]></description>
      <pubDate>Mon, 27 Jan 2025 14:23:00 -0500</pubDate>
      <guid isPermaLink="false">WP-WSJ-0002354638</guid>
      <dc:creator>Jane Doe</dc:creator>
      <wsj:articletype>Middle East News</wsj:articletype>
    </item>
    <item>
      <title>Duplicate guid, different link</title>
      <link>https://www.wsj.com/articles/a-1-copy?mod=rss_worldnews</link>
      <guid isPermaLink="false">WP-WSJ-0002354638</guid>
    </item>
    <item>
      <title>No guid, no date</title>
      <link>https://www.wsj.com/articles/b-2?mod=rss_worldnews</link>
      <description>Second story</description>
    </item>
    <item>
      <title>Duplicate link</title>
      <link>https://www.wsj.com/articles/b-2?mod=rss_worldnews</link>
      <guid isPermaLink="false">different-guid</guid>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>FT - World</title>
  <entry>
    <title type="html">SpaceX catches &lt;b&gt;Starship&lt;/b&gt;</title>
    <id>ce7c8ab1-8337</id>
    <link rel="self" href="https://www.ft.com/api/self/ce7c8ab1"/>
    <link rel="alternate" href="https://www.ft.com/content/ce7c8ab1?syn-25a6b1a6=1"/>
    <summary>Successful landing, key to Mars.</summary>
    <published>2026-11-14T22:33:56Z</published>
    <updated>2026-11-14T23:00:00+01:00</updated>
    <author><name>FT Reporter</name></author>
  </entry>
  <entry>
    <title>Only updated</title>
    <id>d99f635f</id>
    <link href="https://www.ft.com/content/d99f635f"/>
    <summary>No published date here.</summary>
    <updated>2026-11-13T00:00:00Z</updated>
  </entry>
</feed>"""


class TestParseRss(unittest.TestCase):
    def test_feed_and_articles(self):
        feed = news.parse_feed(RSS, "wsj")
        self.assertEqual(feed["title"], "WSJ.com: World News")
        self.assertEqual(len(feed["articles"]), 2)
        first, second = feed["articles"]
        self.assertEqual(first["title"], "AT&T and the Gaza crossing")
        self.assertIn("cross & return — again", first["summary"])
        self.assertNotIn("<p>", first["summary"])
        self.assertEqual(first["author"], "Jane Doe")
        self.assertEqual(first["link"], "https://www.wsj.com/articles/a-1")
        self.assertEqual(first["published_at"],
                         datetime(2025, 1, 27, 19, 23, tzinfo=timezone.utc))
        self.assertEqual(first["raw"]["articletype"], "Middle East News")
        self.assertIsNone(second["guid"])
        self.assertIsNone(second["published_at"])
        self.assertIsNone(second["updated_at"])

    def test_identity_is_stable_and_source_scoped(self):
        a = news.parse_feed(RSS, "wsj")["articles"][0]
        b = news.parse_feed(RSS, "wsj")["articles"][0]
        c = news.parse_feed(RSS, "ft")["articles"][0]
        self.assertEqual(a["id"], b["id"])
        self.assertNotEqual(a["id"], c["id"])
        self.assertEqual(len(a["id"]), 64)

    def test_batch_dedupes_ids(self):
        ids = [a["id"] for a in news.parse_feed(RSS, "wsj")["articles"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_bad_documents(self):
        with self.assertRaises(ValueError):
            news.parse_feed("<RDF/>", "wsj")
        with self.assertRaises(Exception):
            news.parse_feed("<rss><channel>", "wsj")


class TestParseAtom(unittest.TestCase):
    def test_entries(self):
        feed = news.parse_feed(ATOM, "ft")
        self.assertEqual(feed["title"], "FT - World")
        first, second = feed["articles"]
        self.assertEqual(first["link"], "https://www.ft.com/content/ce7c8ab1")
        self.assertEqual(first["title"], "SpaceX catches Starship")
        self.assertEqual(first["summary"], "Successful landing, key to Mars.")
        self.assertEqual(first["author"], "FT Reporter")
        self.assertEqual(first["published_at"],
                         datetime(2026, 11, 14, 22, 33, 56, tzinfo=timezone.utc))
        self.assertEqual(first["updated_at"],
                         datetime(2026, 11, 14, 22, 0, tzinfo=timezone.utc))
        self.assertIsNone(second["published_at"])
        self.assertEqual(second["updated_at"],
                         datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc))

    def test_same_guid_across_sources_differs(self):
        self.assertNotEqual(news.article_id("wsj", "g", None),
                            news.article_id("ft", "g", None))


class TestHelpers(unittest.TestCase):
    def test_plain_text_keeps_semantics(self):
        self.assertEqual(news.plain_text("<p>Stocks   fall &amp; bonds rally — again</p>"),
                         "Stocks fall & bonds rally — again")
        self.assertEqual(news.plain_text(None), "")

    def test_canonical_link_strips_tracking(self):
        self.assertEqual(news.canonical_link("https://www.ft.com/content/x?syn-25a6b1a6=1"),
                         "https://www.ft.com/content/x")
        self.assertEqual(news.canonical_link("https://www.wsj.com/a/b-c?mod=rss_worldnews"),
                         "https://www.wsj.com/a/b-c")
        self.assertEqual(news.canonical_link("https://www.wsj.com/a/b?mod=rss_worldnews&page=2"),
                         "https://www.wsj.com/a/b?page=2")
        self.assertEqual(news.canonical_link("https://www.wsj.com/a/b"),
                         "https://www.wsj.com/a/b")
        self.assertIsNone(news.canonical_link(None))

    def test_link_without_guid_identity_is_tracking_free(self):
        self.assertEqual(
            news.article_id("wsj", None, news.canonical_link("https://a/b?mod=rss")),
            news.article_id("wsj", None, news.canonical_link("https://a/b")))

    def test_timestamps(self):
        self.assertEqual(news.parse_timestamp("Sat, 14 Nov 2026 22:57:26 GMT"),
                         datetime(2026, 11, 14, 22, 57, 26, tzinfo=timezone.utc))
        self.assertEqual(news.parse_timestamp("2026-11-14T22:57:26Z"),
                         datetime(2026, 11, 14, 22, 57, 26, tzinfo=timezone.utc))
        self.assertEqual(news.parse_timestamp("2026-11-14T22:57:26"),
                         datetime(2026, 11, 14, 22, 57, 26, tzinfo=timezone.utc))
        self.assertIsNone(news.parse_timestamp("not a date"))
        self.assertIsNone(news.parse_timestamp(""))
        self.assertIsNone(news.parse_timestamp(None))



class TestTags(unittest.TestCase):
    def test_sections_and_topics(self):
        url = "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"
        tags = news.article_tags(url, "Brent crude jumps as ERCOT braces for heat wave", "")
        self.assertEqual(tags, ["markets", "oil", "power-grid", "texas", "weather"])

    def test_unknown_feed_and_word_boundaries(self):
        tags = news.article_tags("https://www.ft.com/rss/energy", "Reports on toil and coalition", "")
        self.assertEqual(tags, ["energy"])
        self.assertEqual(news.section_tag("https://www.ft.com/rss/home"), "top-stories")


if __name__ == "__main__":
    unittest.main()
