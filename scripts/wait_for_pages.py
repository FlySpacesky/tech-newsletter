#!/usr/bin/env python3
"""Wait until GitHub Pages serves the just-generated edition."""

import json
import os
import time
import urllib.request
from pathlib import Path

site_url = os.environ.get("SITE_URL", "https://flyspacesky.github.io/tech-newsletter/")
edition = json.loads(Path("docs/edition.json").read_text(encoding="utf-8"))["date"]
for attempt in range(30):
    try:
        req = urllib.request.Request(site_url + f"?check={attempt}", headers={"User-Agent": "TechNewsletterBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            if edition in response.read().decode("utf-8", "replace"):
                print(f"GitHub Pages is serving {edition}")
                raise SystemExit(0)
    except Exception as exc:
        print(f"Pages check {attempt + 1}/30: {exc}")
    time.sleep(10)
raise SystemExit("GitHub Pages did not publish the new edition within five minutes; LINE broadcast cancelled.")
