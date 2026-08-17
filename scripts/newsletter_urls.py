#!/usr/bin/env python3
"""Build and validate immutable newsletter edition URLs."""

from __future__ import annotations

import urllib.parse


DEFAULT_SITE_URL = "https://flyspacesky.github.io/tech-newsletter/"


def archive_url_for_date(date: str, site_url: str | None = None) -> str:
    """Return the permanent GitHub Pages URL for one newsletter edition."""

    base_url = (site_url or DEFAULT_SITE_URL).strip()
    if not base_url:
        base_url = DEFAULT_SITE_URL
    if not base_url.endswith("/"):
        base_url += "/"
    return urllib.parse.urljoin(base_url, f"archive/{date}.html")


def validate_archive_url(date: str, edition_url: str) -> None:
    """Reject mutable homepage URLs and malformed edition permalinks."""

    parsed = urllib.parse.urlparse(edition_url)
    expected_suffix = f"/archive/{date}.html"
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not parsed.path.endswith(expected_suffix)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Edition URL must be an HTTPS date-specific archive URL ending in "
            f"{expected_suffix}: {edition_url}"
        )
