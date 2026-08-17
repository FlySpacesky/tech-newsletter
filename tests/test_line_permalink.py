import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from newsletter_urls import archive_url_for_date, validate_archive_url
from prepare_line import build_outbox
from send_line import validate_outbox
from wait_for_pages import edition_date_range


class LinePermalinkTests(unittest.TestCase):
    def setUp(self):
        self.edition = {
            "date": "2026-08-17",
            "start_date": "2026-08-16",
            "end_date": "2026-08-17",
            "source_count": 8,
            "count": 77,
        }

    def test_archive_url_is_date_specific(self):
        self.assertEqual(
            archive_url_for_date("2026-08-17"),
            "https://flyspacesky.github.io/tech-newsletter/"
            "archive/2026-08-17.html",
        )

    def test_custom_site_url_keeps_its_path(self):
        self.assertEqual(
            archive_url_for_date(
                "2026-08-17",
                "https://example.com/newsletters",
            ),
            "https://example.com/newsletters/archive/2026-08-17.html",
        )

    def test_prepare_line_uses_permalink_in_payload_and_message(self):
        outbox = build_outbox(self.edition)
        expected_url = (
            "https://flyspacesky.github.io/tech-newsletter/"
            "archive/2026-08-17.html"
        )
        self.assertEqual(outbox["edition_url"], expected_url)
        self.assertIn(
            f"立即閱讀：{expected_url}",
            outbox["body"]["messages"][0]["text"],
        )
        retry_key, edition_url, body = validate_outbox(
            outbox,
            "2026-08-17",
        )
        self.assertEqual(retry_key, outbox["retry_key"])
        self.assertEqual(edition_url, expected_url)
        self.assertIs(body, outbox["body"])

    def test_mutable_homepage_is_rejected(self):
        outbox = build_outbox(self.edition)
        outbox["edition_url"] = (
            "https://flyspacesky.github.io/tech-newsletter/"
        )
        outbox["body"]["messages"][0]["text"] = (
            "立即閱讀：https://flyspacesky.github.io/tech-newsletter/"
        )
        with self.assertRaisesRegex(ValueError, "date-specific archive"):
            validate_outbox(outbox, "2026-08-17")

    def test_permalink_for_another_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "2026-08-17.html"):
            validate_archive_url(
                "2026-08-17",
                "https://flyspacesky.github.io/tech-newsletter/"
                "archive/2026-08-16.html",
            )

    def test_pages_marker_matches_rendered_date_range(self):
        self.assertEqual(
            edition_date_range(self.edition),
            "2026.08.16—2026.08.17",
        )


if __name__ == "__main__":
    unittest.main()
