#!/usr/bin/env python3
"""Validate and broadcast the current edition through LINE Messaging API."""

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://api.line.me/v2/bot/message"


def retry_key_for_edition(edition: dict) -> str:
    date = edition["date"]
    retry_payload = json.dumps(
        {
            "date": date,
            "start_date": edition.get("start_date", date),
            "end_date": edition.get("end_date", date),
            "count": edition.get("count", 0),
            "per_source": edition.get("per_source", {}),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return str(uuid.UUID(hashlib.md5(f"tech-newsletter:v2:{retry_payload}".encode()).hexdigest()))


def post(path: str, token: str, body: dict, retry_key: str | None = None) -> bool:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    request = urllib.request.Request(API + path, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status not in (200, 202):
                raise RuntimeError(f"LINE returned HTTP {response.status}")
            return True
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", "replace")
        if exc.code == 409 and retry_key:
            print("LINE already accepted this edition; skipping duplicate.")
            return False
        raise RuntimeError(f"LINE returned HTTP {exc.code}: {details}") from exc


def main() -> int:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    site_url = os.environ.get("SITE_URL", "https://flyspacesky.github.io/tech-newsletter/").strip()
    if not token:
        print("Missing LINE_CHANNEL_ACCESS_TOKEN", file=sys.stderr)
        return 2
    edition = json.loads(Path("docs/edition.json").read_text(encoding="utf-8"))
    date = edition["date"]
    start_date = datetime.strptime(edition.get("start_date", date), "%Y-%m-%d").strftime("%Y.%m.%d")
    end_date = datetime.strptime(edition.get("end_date", date), "%Y-%m-%d").strftime("%Y.%m.%d")
    article_count = edition.get("count", 0)
    source_count = edition.get("source_count", 8)
    body = {"messages": [{"type": "text", "text": f"每兩日科技電子報｜{start_date}—{end_date}\n\n已彙整 {source_count} 個來源、共 {article_count} 篇文章，依來源網站分類，包含圖片、摘要與原文連結。\n\n立即閱讀：{site_url}"}]}
    post("/validate/broadcast", token, body)
    retry_key = retry_key_for_edition(edition)
    sent = post("/broadcast", token, body, retry_key)
    if sent:
        print(f"Broadcast accepted for {date}")
    else:
        print(f"Broadcast skipped because edition {date} was already sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
