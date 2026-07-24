import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from generate_newsletter import Item, classify, clean, parse_feed, render


class NewsletterTests(unittest.TestCase):
    def test_parse_rss_and_classify(self):
        raw = b'''<rss><channel><item><title>AI security update</title><link>https://example.com/a</link><description><![CDATA[<b>Important</b> update]]></description><pubDate>Tue, 21 Jul 2026 01:00:00 GMT</pubDate></item></channel></rss>'''
        item = parse_feed(raw, "Example")[0]
        self.assertEqual(item.link, "https://example.com/a")
        self.assertEqual(item.summary, "Important update")
        self.assertEqual(classify(item), "AI、軟體與資安")

    def test_render_escapes_content(self):
        item = Item("A < B", "https://example.com/?a=1&b=2", "safe", "Source", datetime.now(timezone.utc))
        item.category = "商業、產品與新創"
        output = render([item], [{"name": "Source", "homepage": "https://example.com"}], "2026-07-21")
        self.assertIn("A &lt; B", output)
        self.assertIn("a=1&amp;b=2", output)

    def test_clean_truncates(self):
        self.assertTrue(clean("x" * 200, 20).endswith("…"))


if __name__ == "__main__":
    unittest.main()
