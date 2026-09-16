import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import generate_newsletter as g

ROOT = Path(__file__).parents[1]
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]
SOURCE = SOURCES[1]
START, END = date(2026, 9, 15), date(2026, 9, 16)
BASE = "https://www.technice.com.tw/"


def item(number, day=16, source=SOURCE["name"], scope=""):
    return g.Item(f"Article {number}", f"{BASE}issues/ai/{number}/", "Summary", source,
                  datetime(2026, 9, day, 9, tzinfo=g.TZ), "https://example.com/image.jpg", scope)


def listing(numbers):
    cards = "".join(
        f'<article class="elementor-post elementor-grid-item"><div>'
        f'<a href="/issues/ai/{n}/"><img src="/cover.jpg"></a>'
        f'<h2><a href="/issues/ai/{n}/">Article {n}</a></h2></div></article>'
        for n in numbers
    )
    return (f'<article id="the-post">{cards}</article>'
            '<aside><a href="/issues/ai/999/">Sidebar</a></aside>'
            '<a href="/latest/2/">Next</a>').encode()


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 16, 10, tzinfo=g.TZ).astimezone(tz)


class TechNiceTests(unittest.TestCase):
    def test_only_featured_cards_are_candidates(self):
        urls, images = g.listing_candidates(listing([101, 102]), BASE + "latest/", SOURCE)
        self.assertEqual(urls, [item(101).link, item(102).link])
        self.assertEqual(images[g.canonical_key(item(101).link)], BASE + "cover.jpg")
        self.assertEqual(list(g.listing_page_urls(SOURCE))[:3],
                         [BASE + "latest/", BASE + "latest/2/", BASE + "latest/3/"])
        self.assertEqual(SOURCE["feeds"], [])

    def test_pagination_reaches_late_yesterday_and_stops_on_all_old_page(self):
        pages = [listing([101, 102, 103]), listing([104, 105]), listing([106])]
        rows = {101: item(101, 14), 102: item(102, 14), 103: item(103, 15),
                104: item(104, 15), 105: item(105, 16), 106: item(106, 14)}

        def articles(urls, source, errors):
            return [rows[int(url.rstrip("/").rsplit("/", 1)[1])] for url in urls]

        with patch.object(g, "fetch", side_effect=pages) as fetch, \
                patch.object(g, "fetch_article_batch", side_effect=articles):
            found, errors = g.collect_source(SOURCE, START, END)
        self.assertEqual(errors, [])
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual({x.title for x in found}, {"Article 103", "Article 104", "Article 105"})
        self.assertTrue(all(x.collection_scope == SOURCE["collection_scope"] for x in found))

    def test_incomplete_old_page_does_not_stop_early(self):
        with patch.object(g, "fetch", side_effect=[listing([101, 102]), listing([103]), listing([104])]), \
                patch.object(g, "fetch_article_batch", side_effect=[[item(101, 14)], [item(103)], [item(104, 14)], []]):
            found, _ = g.collect_source(SOURCE, START, END)
        self.assertEqual([x.title for x in found], ["Article 103"])

    def test_legacy_cache_is_rejected_but_verified_cache_survives_restart(self):
        legacy = item(101)
        verified = item(102, scope=SOURCE["collection_scope"])
        other = item(103, source=SOURCES[0]["name"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cache.json"
            path.write_text(json.dumps({"items": [g.item_to_dict(x) for x in [legacy, verified, other]]}))
            loaded = g.load_cache(path, SOURCES)
        self.assertEqual({x.title for x in loaded}, {"Article 102", "Article 103"})
        merged = g.richer_item(verified, item(102))
        self.assertEqual(merged.collection_scope, SOURCE["collection_scope"])

    def test_date_window_uses_taiwan_calendar_days(self):
        row = item(101)
        for timestamp, expected in [("2026-09-14T15:59:59+00:00", False),
                                    ("2026-09-14T16:00:00+00:00", True),
                                    ("2026-09-16T15:59:59+00:00", True),
                                    ("2026-09-16T16:00:00+00:00", False)]:
            row.published = datetime.fromisoformat(timestamp)
            self.assertEqual(g.in_edition_window(row, START, END), expected)

    def test_empty_or_changed_featured_page_warns_without_using_sidebar(self):
        with patch.object(g, "fetch", return_value=listing([])), \
                patch.object(g, "fetch_article_batch", return_value=[]):
            found, errors = g.collect_source(SOURCE, START, END)
        self.assertEqual(found, [])
        self.assertTrue(any("no article cards found" in e for e in errors))

    def test_main_excludes_yesterdays_publications_after_cache_merge(self):
        scope = SOURCE["collection_scope"]
        previous = item(101, 15, scope=scope)
        duplicate = item(101, 15, scope=scope)
        duplicate.link += "?utm_source=rss"
        late = item(102, 15, scope=scope)
        today = item(103, scope=scope)
        cached = item(104, 15, scope=scope)
        legacy = item(105, 15)
        other = item(101, 15, source=SOURCES[0]["name"])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "archive").mkdir()
            previous_html = g.render([previous], SOURCES, date(2026, 9, 14), START)
            (output / "archive/2026-09-15.html").write_text(previous_html)
            (output / "article-cache.json").write_text(json.dumps(
                {"items": [g.item_to_dict(x) for x in [previous, cached, legacy, item(106, 14, scope=scope)]]}))
            argv = ["generate_newsletter.py", "--config", str(ROOT / "sources.json"), "--output-dir", temp]
            with patch.object(g, "datetime", FixedDateTime), \
                    patch.object(g, "collect", return_value=([duplicate, late, today, other], [])), \
                    patch.object(sys, "argv", argv):
                self.assertEqual(g.main(), 0)
            edition = json.loads((output / "edition.json").read_text())
            self.assertEqual(edition["count"], 4)
            self.assertEqual(edition["per_source"]["TechNice"], 3)
            self.assertEqual(edition["source_count"], 8)
            keys = g.load_previous_edition_keys(output, END, SOURCES)
            self.assertNotIn((SOURCE["name"], g.canonical_key(previous.link)), keys)
            self.assertIn((other.source, g.canonical_key(other.link)), keys)
            self.assertIn((late.source, g.canonical_key(late.link)), keys)
            self.assertEqual((output / "archive/2026-09-15.html").read_text(), previous_html)
            self.assertIn('id="back-to-top"', (output / "index.html").read_text())
            cache = g.load_cache(output / "article-cache.json", SOURCES)
            self.assertNotIn(legacy.link, [x.link for x in cache])

    def test_missing_previous_archive_keeps_unpublished_articles(self):
        with tempfile.TemporaryDirectory() as temp:
            keys = g.load_previous_edition_keys(Path(temp), START, SOURCES)
        rows = [item(101, 15), item(102)]
        self.assertEqual(g.remove_previous_edition_duplicates(rows, keys), (rows, {}))


if __name__ == "__main__":
    unittest.main()
