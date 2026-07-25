#!/usr/bin/env python3

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

edition = json.loads(
    Path("docs/edition.json").read_text(encoding="utf-8")
)

date = edition["date"]
start_date = datetime.strptime(
    edition.get("start_date", date), "%Y-%m-%d"
).strftime("%Y.%m.%d")
end_date = datetime.strptime(
    edition.get("end_date", date), "%Y-%m-%d"
).strftime("%Y.%m.%d")

outbox_dir = Path("docs/outbox")
outbox_dir.mkdir(parents=True, exist_ok=True)
outbox_file = outbox_dir / f"{date}.json"

# 已建立就絕對不覆寫，確保所有重試使用完全相同的訊息。
if outbox_file.exists():
    print(f"Outbox already exists: {outbox_file}")
    raise SystemExit(0)

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
                "立即閱讀："
                "https://flyspacesky.github.io/tech-newsletter/"
            ),
        }
    ]
}

payload = {
    "date": date,
    "retry_key": retry_key,
    "body": body,
}

outbox_file.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print(f"Prepared LINE outbox: {outbox_file}")
