#!/usr/bin/env python3
"""Build a two-day, source-grouped technology newsletter."""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
UA = "TechNewsletterBot/2.0 (+https://github.com/FlySpacesky/tech-newsletter)"
DEFAULT_SUMMARY = "掌握這則最新科技動態、產業背景與可能影響；詳細內容請參閱原文。"
SKIP_HOSTS = {
    "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "line.me", "twitter.com", "x.com",
    "linkedin.com", "www.linkedin.com", "threads.net", "www.threads.net",
}


@dataclass
class Item:
    title: str
    link: str
    summary: str
    source: str
    published: datetime
    image: str = ""


class PageParser(HTMLParser):
    """Collect links and article metadata from publisher pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_buffer: list[str] = []
        self._title = False
        self._title_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
            if key and values.get("content"):
                self.meta.setdefault(key, values["content"].strip())
        elif tag == "link" and values.get("rel", "").lower() == "canonical" and values.get("href"):
            self.meta.setdefault("canonical", values["href"].strip())
        elif tag == "time" and values.get("datetime"):
            self.meta.setdefault("time:datetime", values["datetime"].strip())
        elif tag == "script" and "ld+json" in values.get("type", "").lower():
            self._in_json_ld = True
            self._json_buffer = []
        elif tag == "title":
            self._title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self._in_json_ld = False
            value = "".join(self._json_buffer).strip()
            if value:
                self.json_ld.append(value)
        elif tag == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._json_buffer.append(data)
        if self._title:
            self._title_text.append(data)

    @property
    def page_title(self) -> str:
        return clean("".join(self._title_text), 140)


def clean(value: str, limit: int = 190) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1].rstrip("，,。；;：: ") + "…"
    return value


def parse_date(value: str) -> datetime | None:
    value = clean(value, 120)
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = re.sub(r"\s+", " ", value.replace("Z", "+00:00")).strip()
    try:
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    patterns = (
        ("%Y/%m/%d %H:%M", r"(\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})"),
        ("%Y/%m/%d", r"(\d{4}/\d{1,2}/\d{1,2})"),
        ("%Y-%m-%d %H:%M", r"(\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})"),
        ("%Y-%m-%d", r"(\d{4}-\d{1,2}-\d{1,2})"),
        ("%Y 年 %m 月 %d 日 %H:%M", r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}:\d{2})"),
        ("%Y 年 %m 月 %d 日", r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"),
        ("%B %d, %Y", r"([A-Za-z]+\s+\d{1,2},\s+\d{4})"),
    )
    for fmt, pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1))
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=TZ).astimezone(timezone.utc)
            except ValueError:
                continue
    return None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(node: ET.Element, *names: str) -> ET.Element | None:
    wanted = set(names)
    return next((c for c in node if local_name(c.tag) in wanted), None)


def text_of(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def first_image_from_entry(entry: ET.Element, description: str) -> str:
    for node in entry.iter():
        tag = local_name(node.tag).lower()
        url = node.get("url") or node.get("href") or ""
        medium = (node.get("medium") or node.get("type") or "").lower()
        if url and (tag in {"thumbnail", "content"} or tag == "enclosure" and "image" in medium):
            return url.strip()
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", description or "", re.I)
    return html.unescape(match.group(1)) if match else ""


def parse_feed(raw: bytes, source: str) -> list[Item]:
    root = ET.fromstring(raw)
    entries = [n for n in root.iter() if local_name(n.tag) in {"item", "entry"}]
    result: list[Item] = []
    for entry in entries:
        title = clean(text_of(child(entry, "title")), 140)
        link_node = child(entry, "link")
        link = (link_node.get("href") or text_of(link_node)).strip() if link_node is not None else ""
        description = text_of(child(entry, "description", "summary", "content", "encoded"))
        published_raw = text_of(child(entry, "pubDate", "published", "updated", "date"))
        published = parse_date(published_raw)
        if title and link.startswith("http") and published:
            result.append(Item(title, link, clean(description), source, published, first_image_from_entry(entry, description)))
    return result


def fetch(url: str, accept_html: bool = False) -> bytes:
    accept = "text/html,application/xhtml+xml,*/*;q=0.8" if accept_html else "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.5"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7"})
    with urllib.request.urlopen(req, timeout=18) as response:
        return response.read()


def decode_page(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def json_ld_objects(values: list[str]) -> list[dict]:
    objects: list[dict] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            objects.append(value)
            if "@graph" in value:
                visit(value["@graph"])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for value in values:
        try:
            visit(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            continue
    return objects


def parse_article(raw: bytes, url: str, source: str) -> Item | None:
    parser = PageParser()
    parser.feed(decode_page(raw))
    meta = parser.meta
    structured = json_ld_objects(parser.json_ld)
    article = next((obj for obj in structured if "article" in str(obj.get("@type", "")).lower()), {})
    title = clean(meta.get("og:title") or meta.get("twitter:title") or str(article.get("headline", "")) or parser.page_title, 140)
    summary = clean(meta.get("og:description") or meta.get("description") or meta.get("twitter:description") or str(article.get("description", "")))
    image_value = article.get("image", "")
    if isinstance(image_value, list):
        image_value = image_value[0] if image_value else ""
    if isinstance(image_value, dict):
        image_value = image_value.get("url", "")
    image = meta.get("og:image") or meta.get("twitter:image") or str(image_value or "")
    date_value = (
        meta.get("article:published_time") or meta.get("datepublished") or meta.get("date")
        or str(article.get("datePublished", "")) or meta.get("time:datetime")
    )
    published = parse_date(date_value)
    canonical = urllib.parse.urljoin(url, meta.get("canonical") or meta.get("og:url") or url)
    if not title or not published:
        return None
    return Item(title, canonical, summary or DEFAULT_SUMMARY, source, published, urllib.parse.urljoin(url, image) if image else "")


def normalize_url(href: str, base: str) -> str:
    url = urllib.parse.urljoin(base, html.unescape(href.strip()))
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("flipboard.com"):
        params = urllib.parse.parse_qs(parsed.query)
        for key in ("url", "target", "source"):
            if params.get(key) and params[key][0].startswith("http"):
                url = params[key][0]
                parsed = urllib.parse.urlparse(url)
                break
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, ""))


def article_candidates(raw: bytes, listing_url: str, source: dict) -> list[str]:
    parser = PageParser()
    parser.feed(decode_page(raw))
    patterns = [re.compile(value, re.I) for value in source.get("article_url_patterns", [])]
    result: list[str] = []
    for href in parser.links:
        url = normalize_url(href, listing_url)
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.hostname in SKIP_HOSTS:
            continue
        if patterns and not any(pattern.search(url) for pattern in patterns):
            continue
        if not source.get("allow_external_links") and urllib.parse.urlparse(source["homepage"]).hostname != parsed.hostname:
            source_host = urllib.parse.urlparse(source["homepage"]).hostname or ""
            if not parsed.hostname.endswith(source_host.removeprefix("www.")):
                continue
        result.append(url)
    return list(dict.fromkeys(result))


def in_edition_window(item: Item, start_day: date, end_day: date) -> bool:
    local_day = item.published.astimezone(TZ).date()
    return start_day <= local_day <= end_day


def collect_source(source: dict, start_day: date, end_day: date) -> tuple[list[Item], list[str]]:
    errors: list[str] = []
    gathered: list[Item] = []
    candidates: list[str] = []
    for feed_url in source.get("feeds", []):
        try:
            gathered.extend(parse_feed(fetch(feed_url), source["name"]))
        except Exception as exc:
            errors.append(f"{source['short_name']} feed {feed_url}: {exc}")
    for listing_url in source.get("listing_pages", []):
        try:
            candidates.extend(article_candidates(fetch(listing_url, accept_html=True), listing_url, source))
        except Exception as exc:
            errors.append(f"{source['short_name']} listing {listing_url}: {exc}")

    feed_links = {canonical_key(item.link) for item in gathered}
    candidates = [url for url in dict.fromkeys(candidates) if canonical_key(url) not in feed_links]
    candidates = candidates[: int(source.get("max_candidates", 90))]
    if candidates:
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = {pool.submit(fetch, url, True): url for url in candidates}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    item = parse_article(future.result(), url, source["name"])
                    if item:
                        gathered.append(item)
                except Exception as exc:
                    errors.append(f"{source['short_name']} article {url}: {exc}")

    dedup: dict[str, Item] = {}
    for item in sorted(gathered, key=lambda row: row.published, reverse=True):
        if in_edition_window(item, start_day, end_day):
            key = canonical_key(item.link)
            current = dedup.get(key)
            if not current or len(item.summary) > len(current.summary):
                dedup[key] = item
    return sorted(dedup.values(), key=lambda row: row.published, reverse=True), errors


def canonical_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def collect(config: dict, now: datetime) -> tuple[list[Item], list[str]]:
    end_day = now.astimezone(TZ).date()
    start_day = date.fromordinal(end_day.toordinal() - 1)
    all_items: list[Item] = []
    errors: list[str] = []
    for source in config["sources"]:
        items, source_errors = collect_source(source, start_day, end_day)
        all_items.extend(items)
        errors.extend(source_errors)
        print(f"{source['short_name']}: {len(items)} article(s)", file=sys.stderr)
    return all_items, errors


def story(item: Item) -> str:
    stamp = item.published.astimezone(TZ).strftime("%Y.%m.%d %H:%M")
    image = (
        f'<img src="{html.escape(item.image, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        if item.image else '<div class="image-fallback" aria-hidden="true">NEWS</div>'
    )
    return f'''<article class="story">
  <a class="story-image" href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">{image}</a>
  <div class="story-copy">
    <time>{stamp}</time>
    <h3><a href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(item.title)}</a></h3>
    <p>{html.escape(item.summary or DEFAULT_SUMMARY)}</p>
    <a class="read" href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">閱讀原文 →</a>
  </div>
</article>'''


def render(items: list[Item], sources: list[dict], start_day: date, end_day: date) -> str:
    by_source = {source["name"]: [] for source in sources}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    sections: list[str] = []
    nav: list[str] = []
    for index, source in enumerate(sources, start=1):
        source_items = by_source.get(source["name"], [])
        section_id = f"source-{index}"
        nav.append(f'<a href="#{section_id}">{html.escape(source["short_name"])} <b>{len(source_items)}</b></a>')
        if source_items:
            body = "\n".join(story(item) for item in source_items)
        else:
            body = '<p class="empty">今天與昨天未找到可確認發布日期的文章。</p>'
        sections.append(f'''<section id="{section_id}">
  <div class="section-head"><div><span>來源 {index:02d}</span><h2>{html.escape(source["name"])}</h2></div><strong>{len(source_items)} 篇</strong></div>
  <div class="stories">{body}</div>
</section>''')
    date_range = f"{start_day:%Y.%m.%d}—{end_day:%Y.%m.%d}"
    source_names = "、".join(source["short_name"] for source in sources)
    count = len(items)
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>每兩日科技電子報｜{date_range}</title>
<style>
:root{{--ink:#182230;--muted:#667085;--brand:#7057d9;--brand2:#ef7d62;--paper:#fff;--bg:#f6f5f2;--line:#e7e3dc}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif;line-height:1.65}}
a{{color:inherit}}.wrap{{width:min(1040px,calc(100% - 28px));margin:auto}}
header{{padding:52px 0 40px;background:#1e2442;color:#fff;border-bottom:8px solid var(--brand2)}}.kicker{{font-size:13px;letter-spacing:.16em;color:#d8d5ff}}
h1{{font-size:clamp(36px,7vw,68px);line-height:1.03;margin:11px 0 16px;letter-spacing:-.04em}}.date{{font-size:clamp(20px,4vw,32px);font-weight:800;color:#ffd3c8}}
.stats{{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 0}}.stat{{padding:9px 14px;border:1px solid #ffffff38;border-radius:999px;background:#ffffff12}}.stat b{{font-size:20px;margin-right:5px}}
.source-line{{margin:18px 0 0;color:#d4d7e5;font-size:14px}}nav{{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line)}}nav .wrap{{display:flex;gap:8px;overflow:auto;padding-top:12px;padding-bottom:12px}}
nav a{{white-space:nowrap;text-decoration:none;border:1px solid var(--line);border-radius:999px;padding:7px 11px;font-size:13px}}nav b{{color:var(--brand)}}
main{{padding:34px 0 56px}}section{{scroll-margin-top:82px;margin:0 0 40px}}.section-head{{display:flex;align-items:end;justify-content:space-between;border-bottom:2px solid var(--ink);padding-bottom:10px;margin-bottom:15px}}
.section-head span{{font-size:11px;letter-spacing:.16em;color:var(--brand);font-weight:800}}h2{{font-size:clamp(24px,4vw,34px);line-height:1.2;margin:2px 0 0}}.section-head strong{{color:var(--muted)}}
.stories{{display:grid;gap:13px}}.story{{display:grid;grid-template-columns:210px 1fr;min-height:150px;background:var(--paper);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 7px 24px rgba(24,34,48,.05)}}
.story-image{{display:block;background:linear-gradient(135deg,#ddd8ff,#ffd9cf);min-height:150px;overflow:hidden}}.story-image img{{width:100%;height:100%;min-height:150px;object-fit:cover;display:block}}.image-fallback{{display:grid;place-items:center;height:100%;min-height:150px;color:#7067a4;font-weight:900;letter-spacing:.14em}}
.story-copy{{padding:17px 20px}}time{{font-size:12px;color:var(--brand);font-weight:800}}h3{{font-size:20px;line-height:1.4;margin:5px 0 7px}}h3 a{{text-decoration:none}}h3 a:hover{{text-decoration:underline}}
.story p{{margin:0 0 8px;color:#4b5565}}.read{{font-size:13px;color:var(--brand);font-weight:800;text-decoration:none}}.empty{{padding:22px;background:#fff;border:1px dashed #c9c4bb;border-radius:12px;color:var(--muted)}}
footer{{background:#fff;border-top:1px solid var(--line);padding:26px 0 42px;color:var(--muted);font-size:13px}}
@media(max-width:680px){{header{{padding:40px 0 32px}}.story{{grid-template-columns:118px 1fr}}.story-image,.story-image img,.image-fallback{{min-height:132px}}.story-copy{{padding:13px}}h3{{font-size:17px}}.story p{{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}}}
@media(max-width:440px){{.story{{grid-template-columns:1fr}}.story-image,.story-image img,.image-fallback{{height:178px}}}}
</style></head><body>
<header><div class="wrap"><div class="kicker">TWO-DAY TECHNOLOGY NEWSLETTER</div><h1>每兩日科技電子報</h1><div class="date">{date_range}</div>
<div class="stats"><div class="stat"><b>{len(sources)}</b>個來源</div><div class="stat"><b>{count}</b>篇文章</div><div class="stat">最多 <b>3</b>頁</div></div>
<p class="source-line"><strong>本期來源：</strong>{html.escape(source_names)}</p></div></header>
<nav><div class="wrap">{''.join(nav)}</div></nav>
<main class="wrap">{''.join(sections)}</main>
<footer><div class="wrap">每日約 10:07（Asia/Taipei）更新，內容涵蓋今天與昨天；各來源讀取可用的最新列表，支援分頁者最多讀取第 1–3 頁，並依網址去重。摘要與圖片取自來源公開中繼資料，內容以原文為準。</div></footer>
</body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="sources.json")
    parser.add_argument("--output-dir", default="docs")
    args = parser.parse_args()
    now = datetime.now(TZ)
    end_day = now.date()
    start_day = date.fromordinal(end_day.toordinal() - 1)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if len(config.get("sources", [])) != 8:
        print("sources.json must contain exactly 8 sources", file=sys.stderr)
        return 2
    items, errors = collect(config, now)
    if not items:
        for message in errors:
            print(message, file=sys.stderr)
        print("No articles matched today or yesterday; refusing to overwrite the current edition.", file=sys.stderr)
        return 2
    output = Path(args.output_dir)
    (output / "archive").mkdir(parents=True, exist_ok=True)
    edition = end_day.strftime("%Y-%m-%d")
    content = render(items, config["sources"], start_day, end_day)
    (output / "index.html").write_text(content, encoding="utf-8")
    (output / "archive" / f"{edition}.html").write_text(content, encoding="utf-8")
    counts = {source["short_name"]: sum(1 for item in items if item.source == source["name"]) for source in config["sources"]}
    payload = {"date": edition, "start_date": start_day.isoformat(), "end_date": end_day.isoformat(), "source_count": 8, "count": len(items), "per_source": counts, "warnings": len(errors)}
    (output / "edition.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for message in errors:
        print("WARNING:", message, file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
