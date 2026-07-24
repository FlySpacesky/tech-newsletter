import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from generate_newsletter import Item, article_candidates, clean, parse_article, parse_date, parse_feed, render


class NewsletterTests(unittest.TestCase):
    def test_parse_rss_with_image(self):
        raw = b'''<rss><channel><item><title>AI security update</title><link>https://example.com/a</link><description><![CDATA[<b>Important</b> update<img src="https://img.example/a.jpg">]]></description><pubDate>Fri, 24 Jul 2026 01:00:00 GMT</pubDate></item></channel></rss>'''
        item = parse_feed(raw, "Example")[0]
        self.assertEqual(item.link, "https://example.com/a")
        self.assertEqual(item.summary, "Important update")
        self.assertEqual(item.image, "https://img.example/a.jpg")

    def test_parse_article_metadata(self):
        raw = b'''<html><head><meta property="og:title" content="New AI chip"><meta property="og:description" content="Summary"><meta property="og:image" content="/cover.jpg"><meta property="article:published_time" content="2026-07-24T09:30:00+08:00"><link rel="canonical" href="https://example.com/article/1"></head></html>'''
        item = parse_article(raw, "https://example.com/article/1?utm=x", "Example")
        self.assertIsNotNone(item)
        self.assertEqual(item.title, "New AI chip")
        self.assertEqual(item.image, "https://example.com/cover.jpg")
        self.assertEqual(item.link, "https://example.com/article/1")

    def test_listing_collects_matching_links(self):
        raw = b'''<a href="/article/1">one</a><a href="/tag/ai">tag</a>'''
        source = {"homepage": "https://example.com", "article_url_patterns": [r"^https://example\.com/article/\d+"], "allow_external_links": False}
        self.assertEqual(article_candidates(raw, "https://example.com/latest", source), ["https://example.com/article/1"])

    def test_date_parser_uses_arabic_dates(self):
        self.assertEqual(parse_date("2026 年 07 月 24 日 09:30").astimezone(timezone.utc).date(), date(2026, 7, 24))

    def test_render_groups_by_source_and_has_left_image(self):
        item = Item("A < B", "https://example.com/?a=1&b=2", "safe", "Source One", datetime(2026, 7, 24, tzinfo=timezone.utc), "https://example.com/a.jpg")
        sources = [{"name": "Source One", "short_name": "One", "homepage": "https://example.com"}]
        output = render([item], sources, date(2026, 7, 23), date(2026, 7, 24))
        self.assertIn("每兩日科技電子報", output)
        self.assertIn("2026.07.23—2026.07.24", output)
        self.assertIn('class="story-image"', output)
        self.assertIn("A &lt; B", output)
        self.assertIn("a=1&amp;b=2", output)

    def test_clean_truncates(self):
        self.assertTrue(clean("x" * 200, 20).endswith("…"))


if __name__ == "__main__":
    unittest.main()
