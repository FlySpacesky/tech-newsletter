#!/usr/bin/env python3
"""Send the prepared newsletter through LINE with safe retries."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from newsletter_urls import validate_archive_url


LINE_API = "https://api.line.me/v2/bot/message"
TZ = ZoneInfo("Asia/Taipei")

# 第一次立即執行，之後等待5、15、30、60秒。
RETRY_DELAYS = (0, 5, 15, 30, 60)

# 只有暫時性伺服器錯誤才重試。
RETRYABLE_HTTP_CODES = {500, 502, 503, 504}


def send_request(
    path: str,
    token: str,
    body: dict,
    retry_key: str | None = None,
    accept_conflict: bool = False,
) -> dict:
    """Call one LINE API endpoint with exponential retry."""

    encoded_body = json.dumps(
        body,
        ensure_ascii=False,
    ).encode("utf-8")

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            print(
                f"Waiting {delay} seconds before retry "
                f"{attempt}/{len(RETRY_DELAYS)}..."
            )
            time.sleep(delay)

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # 只有真正發送訊息的 /broadcast 才能加 Retry Key。
        if retry_key:
            headers["X-Line-Retry-Key"] = retry_key

        request = urllib.request.Request(
            LINE_API + path,
            data=encoded_body,
            headers=headers,
            method="POST",
        )

        print(
            f"LINE {path} attempt "
            f"{attempt}/{len(RETRY_DELAYS)}"
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
            ) as response:
                status = response.status
                request_id = response.headers.get(
                    "x-line-request-id",
                    "",
                )

                if status not in (200, 202):
                    raise RuntimeError(
                        f"Unexpected LINE HTTP status: {status}"
                    )

                return {
                    "result": "accepted",
                    "http_status": status,
                    "request_id": request_id,
                    "accepted_request_id": "",
                }

        except urllib.error.HTTPError as exc:
            details = exc.read().decode(
                "utf-8",
                "replace",
            )

            request_id = exc.headers.get(
                "x-line-request-id",
                "",
            )

            accepted_request_id = exc.headers.get(
                "x-line-accepted-request-id",
                "",
            )

            # 409 表示相同的 Retry Key 已經被 LINE 接受。
            # 這不是失敗，必須視為成功，避免再次廣播。
            if exc.code == 409 and accept_conflict:
                print(
                    "LINE already accepted this edition; "
                    "treating HTTP 409 as success."
                )

                return {
                    "result": "already_accepted",
                    "http_status": 409,
                    "request_id": request_id,
                    "accepted_request_id": accepted_request_id,
                }

            # 只有500、502、503、504進行重試。
            if (
                exc.code in RETRYABLE_HTTP_CODES
                and attempt < len(RETRY_DELAYS)
            ):
                print(
                    f"Temporary LINE HTTP {exc.code}: "
                    f"{details}",
                    file=sys.stderr,
                )
                continue

            # 400、401、403、429等錯誤不在這裡盲目重試。
            raise RuntimeError(
                f"LINE returned HTTP {exc.code}: {details}"
            ) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
        ) as exc:
            if attempt < len(RETRY_DELAYS):
                print(
                    f"Temporary LINE network error: {exc}",
                    file=sys.stderr,
                )
                continue

            raise RuntimeError(
                "LINE request still failed after "
                f"{len(RETRY_DELAYS)} attempts: {exc}"
            ) from exc

    raise RuntimeError("LINE retry loop ended unexpectedly.")


def write_receipt(
    date: str,
    status: str,
    result: dict,
    retry_key: str,
    edition_url: str,
) -> Path:
    """Save an auditable success receipt."""

    receipt_dir = Path("docs/delivery")
    receipt_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    receipt_file = receipt_dir / f"{date}.json"
    temporary_file = receipt_dir / f"{date}.json.tmp"

    receipt = {
        "date": date,
        "status": status,
        "accepted_at": datetime.now(TZ).isoformat(),
        "http_status": result.get("http_status"),
        "request_id": result.get("request_id", ""),
        "accepted_request_id": result.get(
            "accepted_request_id",
            "",
        ),
        "retry_key": retry_key,
        "edition_url": edition_url,
        "github_run_id": os.environ.get(
            "GITHUB_RUN_ID",
            "",
        ),
        "github_run_attempt": os.environ.get(
            "GITHUB_RUN_ATTEMPT",
            "",
        ),
        "github_event_name": os.environ.get(
            "GITHUB_EVENT_NAME",
            "",
        ),
    }

    temporary_file.write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    # 使用 replace，避免寫到一半產生不完整JSON。
    temporary_file.replace(receipt_file)

    return receipt_file


def validate_outbox(outbox: dict, today: str) -> tuple[str, str, dict]:
    """Validate that LINE will receive today's immutable archive URL."""

    outbox_date = str(outbox.get("date", ""))
    retry_key = str(outbox.get("retry_key", ""))
    edition_url = str(outbox.get("edition_url", "")).strip()
    body = outbox.get("body")

    if outbox_date != today:
        raise ValueError(
            f"Outbox date mismatch: {outbox_date} != {today}"
        )
    if not retry_key:
        raise ValueError("Outbox does not contain retry_key.")
    if not isinstance(body, dict):
        raise ValueError("Outbox does not contain a valid body.")
    if not edition_url:
        raise ValueError("Outbox does not contain edition_url.")

    validate_archive_url(today, edition_url)
    messages = body.get("messages")
    if not isinstance(messages, list) or not any(
        isinstance(message, dict)
        and edition_url in str(message.get("text", ""))
        for message in messages
    ):
        raise ValueError(
            "LINE message does not contain the validated edition_url."
        )

    return retry_key, edition_url, body


def main() -> int:
    token = os.environ.get(
        "LINE_CHANNEL_ACCESS_TOKEN",
        "",
    ).strip()

    if not token:
        print(
            "Missing LINE_CHANNEL_ACCESS_TOKEN.",
            file=sys.stderr,
        )
        return 2

    today = datetime.now(TZ).strftime("%Y-%m-%d")

    receipt_file = Path(
        f"docs/delivery/{today}.json"
    )

    # 第二層防呆：
    # 即使 workflow 的檢查失效，send_line.py 本身也不重複發送。
    if receipt_file.exists():
        print(
            f"Delivery receipt already exists: "
            f"{receipt_file}"
        )
        print(
            "Today's LINE broadcast is skipped."
        )
        return 0

    outbox_file = Path(
        f"docs/outbox/{today}.json"
    )

    if not outbox_file.exists():
        print(
            f"Missing LINE outbox: {outbox_file}",
            file=sys.stderr,
        )
        print(
            "Run scripts/prepare_line.py first.",
            file=sys.stderr,
        )
        return 2

    try:
        outbox = json.loads(
            outbox_file.read_text(
                encoding="utf-8",
            )
        )
    except json.JSONDecodeError as exc:
        print(
            f"Invalid outbox JSON: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        retry_key, edition_url, body = validate_outbox(
            outbox,
            today,
        )
    except ValueError as exc:
        print(
            f"Invalid LINE outbox: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        # 第一步只驗證訊息格式，不會真正廣播。
        send_request(
            "/validate/broadcast",
            token,
            body,
        )
        print("LINE broadcast validation successful.")

        # 第二步才是真正廣播。
        result = send_request(
            "/broadcast",
            token,
            body,
            retry_key=retry_key,
            accept_conflict=True,
        )

        status = result["result"]

        receipt_path = write_receipt(
            today,
            status,
            result,
            retry_key,
            edition_url,
        )

        if status == "accepted":
            print(
                f"Broadcast accepted for {today}"
            )
        else:
            print(
                f"Broadcast for {today} had already "
                "been accepted by LINE."
            )

        print(
            f"Delivery receipt saved: {receipt_path}"
        )

        return 0

    except Exception as exc:
        # 讓 GitHub Actions 顯示紅色失敗。
        # 下一個補償排程會再次執行相同 outbox。
        print(
            f"LINE broadcast failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
