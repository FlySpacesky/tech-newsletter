#!/usr/bin/env python3

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from newsletter_urls import archive_url_for_date


def build_outbox(edition: dict, site_url: str | None = None) -> dict:
    """Build one immutable, retry-safe LINE message for an edition."""

    date = edition["date"]
    start_date = datetime.strptime(
        edition.get("start_date", date), "%Y-%m-%d"
    ).strftime("%Y.%m.%d")
    end_date = datetime.strptime(
        edition.get("end_date", date), "%Y-%m-%d"
    ).strftime("%Y.%m.%d")
    edition_url = archive_url_for_date(date, site_url)
    retry_source = f"tech-newsletter:broadcast:{date}:v1"
    retry_key = str(
        uuid.UUID(hashlib.md5(retry_source.encode()).hexdigest())
    )
    body = {
        "messages": [
            {
                "type": "text",
                "text": (
                    f"每兩日科技電子報｜{start_date}—{end_date}\n\n"
                    f"已彙整 {edition.get('source_count', 8)} 個來源、"
                    f"共 {edition.get('count', 0)} 篇文章。\n\n"
                    f"立即閱讀：{edition_url}"
                ),
            }
        ]
    }
    return {
        "date": date,
        "edition_url": edition_url,
        "retry_key": retry_key,
        "body": body,
    }


def main() -> int:
    edition = json.loads(
        Path("docs/edition.json").read_text(encoding="utf-8")
    )
    date = edition["date"]
    outbox_dir = Path("docs/outbox")
    outbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_file = outbox_dir / f"{date}.json"

    # 已建立就絕對不覆寫，確保所有重試使用完全相同的訊息。
    if outbox_file.exists():
        print(f"Outbox already exists: {outbox_file}")
        return 0

    payload = build_outbox(
        edition,
        os.environ.get("SITE_URL"),
    )
    outbox_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared LINE outbox: {outbox_file}")
    print(f"Edition permalink: {payload['edition_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
