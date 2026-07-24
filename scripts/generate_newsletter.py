#!/usr/bin/env python3
"""Fetch configured RSS/Atom feeds and build a responsive Chinese newsletter."""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
UA = "TechNewsletterBot/1.0 (+https://github.com/FlySpacesky/tech-newsletter)"

CATEGORIES = {
    "AI、軟體與資安": "ai agent 模型 llm 軟體 資安 security 雲端 cloud 開發 api 資料庫 database",
    "晶片、硬體與基礎設施": "晶片 半導體 台積電 tsmc gpu cpu nvidia 記憶體 伺服器 資料中心 電信 硬體",
    "商業、產品與新創": "新創 募資 收購 併購 產品 電商 金融 市場 營收 訂閱 商業 政策 法規",
}


@dataclass
class Item:
    title: str
    link: str
    summary: str
    source: str
    published: datetime
    category: str = "商業、產品與新創"


def text_of(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def clean(value: str, limit: int = 170) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1].rstrip("，,。；;：: ") + "…"
    return value


def parse_date(value: str) -> datetime:
    value = (value or "").strip()
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(node: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    return next((c for c in node if local_name(c.tag) in wanted), None)


def parse_feed(raw: bytes, source: str) -> list[Item]:
    root = ET.fromstring(raw)
    entries = [n for n in root.iter() if local_name(n.tag) in {"item", "entry"}]
    items: list[Item] = []
    for entry in entries:
        title = clean(text_of(child(entry, "title")), 120)
        link_node = child(entry, "link")
        link = ""
        if link_node is not None:
            link = (link_node.get("href") or text_of(link_node)).strip()
        description = text_of(child(entry, "description", "summary", "content", "encoded"))
        published = text_of(child(entry, "pubDate", "published", "updated", "date"))
        if title and link.startswith("http"):
            items.append(Item(title, link, clean(description), source, parse_date(published)))
    return items


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()


def classify(item: Item) -> str:
    haystack = f"{item.title} {item.summary}".lower()
    scores = {name: sum(1 for k in words.split() if k in haystack) for name, words in CATEGORIES.items()}
    return max(scores, key=scores.get) if max(scores.values()) else item.category


def collect(config: dict, now: datetime, max_items: int) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    for source in config["sources"]:
        source_items: list[Item] = []
        for url in source["feeds"]:
            try:
                source_items = parse_feed(fetch(url), source["name"])
                if source_items:
                    break
            except Exception as exc:  # one broken publisher must not break the edition
                errors.append(f"{source['name']} {url}: {exc}")
        items.extend(source_items)

    dedup: dict[str, Item] = {}
    for item in sorted(items, key=lambda x: x.published, reverse=True):
        key = re.sub(r"\?.*$", "", item.link).rstrip("/")
        dedup.setdefault(key, item)
    items = list(dedup.values())
    cutoff = now.astimezone(timezone.utc) - timedelta(days=2)
    recent = [i for i in items if i.published >= cutoff]
    if len(recent) < min(6, max_items):
        cutoff = now.astimezone(timezone.utc) - timedelta(days=7)
        recent = [i for i in items if i.published >= cutoff]
    selected = recent[:max_items]
    for item in selected:
        item.category = classify(item)
        if not item.summary:
            item.summary = "掌握這則最新科技動態、其產業背景與可能影響；詳細內容請參閱原文。"
    return selected, errors


def story(item: Item) -> str:
    date = item.published.astimezone(TZ).strftime("%m/%d %H:%M")
    return f'''<article class="story">
  <div class="meta"><span>{html.escape(item.source)}</span><time>{date}</time></div>
  <h3>{html.escape(item.title)}</h3>
  <p>{html.escape(item.summary)}</p>
  <a href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">閱讀原文 →</a>
</article>'''


def render(items: list[Item], sources: list[dict], edition_date: str) -> str:
    groups = []
    for category in CATEGORIES:
        cards = "\n".join(story(i) for i in items if i.category == category)
        if cards:
            groups.append(f'<section><h2>{html.escape(category)}</h2><div class="grid">{cards}</div></section>')
    source_links = "".join(f'<a href="{html.escape(s["homepage"], quote=True)}">{html.escape(s["name"])}</a>' for s in sources)
    count = len(items)
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>科技雷達｜{edition_date}</title>
<style>
:root{{--ink:#172033;--muted:#667085;--brand:#5b4cf0;--paper:#fff;--bg:#f2f4f8;--line:#e4e7ec}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;line-height:1.65}}
.wrap{{width:min(1080px,calc(100% - 28px));margin:auto}}header{{padding:62px 0 46px;background:linear-gradient(135deg,#111827,#312e81 58%,#6d28d9);color:#fff}}
.eyebrow{{font-size:13px;letter-spacing:.14em;text-transform:uppercase;opacity:.78}}h1{{font-size:clamp(32px,6vw,60px);line-height:1.08;margin:10px 0 16px}}header p{{max-width:720px;margin:0;color:#ddd6fe}}
main{{padding:34px 0 52px}}section{{margin:0 0 38px}}h2{{font-size:24px;margin:0 0 16px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
.story{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 30px rgba(16,24,40,.05)}}.meta{{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:12px}}
h3{{font-size:20px;line-height:1.4;margin:12px 0 9px}}.story p{{color:#475467;margin:0 0 15px}}.story a{{color:var(--brand);font-weight:700;text-decoration:none}}
footer{{background:#fff;border-top:1px solid var(--line);padding:28px 0 44px;color:var(--muted);font-size:13px}}.sources{{display:flex;gap:12px;flex-wrap:wrap;margin-top:9px}}.sources a{{color:#475467}}
@media(max-width:700px){{header{{padding:44px 0 36px}}.grid{{grid-template-columns:1fr}}.story{{padding:18px}}}}
</style></head><body>
<header><div class="wrap"><div class="eyebrow">Daily Tech Radar · {edition_date}</div><h1>今天的科技變化，濃縮成一封信</h1><p>從固定追蹤來源整理 {count} 則近期新聞，依主題分類並保留原文連結。</p></div></header>
<main class="wrap">{''.join(groups)}</main>
<footer><div class="wrap"><strong>固定追蹤來源</strong><div class="sources">{source_links}</div><p>自動彙整時間：每天約 10:00（Asia/Taipei）。摘要由來源公開資訊整理，請以原文為準。</p></div></footer>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="sources.json")
    parser.add_argument("--output-dir", default="docs")
    parser.add_argument("--max-items", type=int, default=12)
    args = parser.parse_args()
    now = datetime.now(TZ)
    edition = now.strftime("%Y-%m-%d")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    items, errors = collect(config, now, args.max_items)
    if len(items) < 3:
        for message in errors:
            print(message, file=sys.stderr)
        print(f"Only {len(items)} usable items; refusing to overwrite the current edition.", file=sys.stderr)
        return 2
    output = Path(args.output_dir)
    (output / "archive").mkdir(parents=True, exist_ok=True)
    content = render(items, config["sources"], edition)
    (output / "index.html").write_text(content, encoding="utf-8")
    (output / "archive" / f"{edition}.html").write_text(content, encoding="utf-8")
    (output / "edition.json").write_text(json.dumps({"date": edition, "count": len(items)}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"date": edition, "count": len(items), "feed_errors": len(errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
