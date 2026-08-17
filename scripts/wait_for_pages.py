#!/usr/bin/env python3
"""Wait until GitHub Pages serves the just-generated edition."""

import json
import os
import time
import urllib.request
from pathlib import Path

from newsletter_urls import archive_url_for_date


def edition_date_range(edition: dict) -> str:
    """Return the date marker rendered in the edition header and title."""

    date = edition["date"]
    start_date = edition.get("start_date", date).replace("-", ".")
    end_date = edition.get("end_date", date).replace("-", ".")
    return f"{start_date}—{end_date}"


def main() -> int:
    edition = json.loads(
        Path("docs/edition.json").read_text(encoding="utf-8")
    )
    edition_date = edition["date"]
    expected_range = edition_date_range(edition)
    edition_url = archive_url_for_date(
        edition_date,
        os.environ.get("SITE_URL"),
    )

    for attempt in range(30):
        try:
            request = urllib.request.Request(
                edition_url + f"?check={attempt}",
                headers={"User-Agent": "TechNewsletterBot/1.0"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                content = response.read().decode("utf-8", "replace")
                if expected_range in content:
                    print(
                        "GitHub Pages is serving edition "
                        f"{edition_date}: {edition_url}"
                    )
                    return 0
        except Exception as exc:
            print(f"Pages check {attempt + 1}/30: {exc}")
        time.sleep(10)

    raise SystemExit(
        "GitHub Pages did not publish the date-specific edition within five "
        "minutes; LINE broadcast cancelled."
    )


if __name__ == "__main__":
    raise SystemExit(main())
