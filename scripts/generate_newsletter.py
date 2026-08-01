#!/usr/bin/env python3
"""Build a two-day, source-grouped technology newsletter."""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
        self.link_images: dict[str, str] = {}
        self.meta: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._in_json_ld = False
        self._json_buffer: list[str] = []
        self._title = False
        self._title_text: list[str] = []
        self._anchor_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
            self._anchor_stack.append(values["href"])
        elif tag == "img" and self._anchor_stack:
            image = (
                values.get("data-lazy-src") or values.get("data-src")
                or values.get("data-original") or values.get("src")
            )
            if not image and values.get("srcset"):
                image = values["srcset"].split(",", 1)[0].strip().split(" ", 1)[0]
            if image and not image.startswith("data:"):
                self.link_images.setdefault(self._anchor_stack[-1], image)
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
        if tag == "a" and self._anchor_stack:
            self._anchor_stack.pop()
        elif tag == "script" and self._in_json_ld:
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


def fetch(url: str, accept_html: bool = False, attempts: int = 3) -> bytes:
    accept = "text/html,application/xhtml+xml,*/*;q=0.8" if accept_html else "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.5"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=18) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 >= attempts:
                raise
        time.sleep(0.75 * (2 ** attempt))
    raise RuntimeError(f"Unable to fetch {url}")


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


def listing_candidates(raw: bytes, listing_url: str, source: dict) -> tuple[list[str], dict[str, str]]:
    parser = PageParser()
    parser.feed(decode_page(raw))
    patterns = [re.compile(value, re.I) for value in source.get("article_url_patterns", [])]
    result: list[str] = []
    images: dict[str, str] = {}
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
        image = parser.link_images.get(href, "")
        if image:
            resolved_image = urllib.parse.urljoin(listing_url, html.unescape(image))
            if resolved_image.startswith("http://"):
                resolved_image = "https://" + resolved_image.removeprefix("http://")
            images.setdefault(canonical_key(url), resolved_image)
    return list(dict.fromkeys(result)), images


def article_candidates(raw: bytes, listing_url: str, source: dict) -> list[str]:
    return listing_candidates(raw, listing_url, source)[0]


def in_edition_window(item: Item, start_day: date, end_day: date) -> bool:
    local_day = item.published.astimezone(TZ).date()
    return start_day <= local_day <= end_day


def item_to_dict(item: Item) -> dict:
    return {
        "title": item.title,
        "link": item.link,
        "summary": item.summary,
        "source": item.source,
        "published": item.published.astimezone(timezone.utc).isoformat(),
        "image": item.image,
    }


def load_cache(path: Path, sources: list[dict]) -> list[Item]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("items", []) if isinstance(payload, dict) else []
    known_sources = {source["name"] for source in sources}
    result: list[Item] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("source") not in known_sources:
            continue
        published = parse_date(str(row.get("published", "")))
        link = str(row.get("link", ""))
        title = clean(str(row.get("title", "")), 140)
        if published and title and link.startswith("http"):
            result.append(
                Item(
                    title,
                    link,
                    clean(str(row.get("summary", ""))) or DEFAULT_SUMMARY,
                    str(row["source"]),
                    published,
                    str(row.get("image", "")),
                )
            )
    return result


def merge_items(groups: list[list[Item]], start_day: date, end_day: date) -> list[Item]:
    merged: dict[tuple[str, str], Item] = {}
    for group in groups:
        for item in group:
            if not in_edition_window(item, start_day, end_day):
                continue
            key = (item.source, canonical_key(item.link))
            current = merged.get(key)
            if current is None:
                merged[key] = item
                continue
            merged[key] = richer_item(current, item)
    return sorted(merged.values(), key=lambda row: row.published, reverse=True)


def richer_item(current: Item, candidate: Item) -> Item:
    """Combine feed and article-page metadata instead of discarding either."""
    summary = max((current.summary, candidate.summary), key=lambda value: len(value or ""))
    image = current.image or candidate.image
    link = candidate.link if "utm_" not in candidate.link else current.link
    title = current.title if len(current.title) >= len(candidate.title) else candidate.title
    published = min(current.published, candidate.published)
    return Item(title, link, summary or DEFAULT_SUMMARY, current.source, published, image)


def listing_page_urls(source: dict):
    pagination = source.get("pagination")
    if not pagination:
        yield from source.get("listing_pages", [])
        return
    start_page = int(pagination.get("start_page", 1))
    max_pages = int(pagination.get("safety_max_pages", 100))
    first_url = pagination.get("first_url")
    template = pagination["url_template"]
    for page in range(start_page, start_page + max_pages):
        if page == start_page and first_url:
            yield first_url
        else:
            yield template.format(page=page)


def date_hint_from_url(url: str) -> date | None:
    match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})/", urllib.parse.urlparse(url).path)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def fetch_article_batch(urls: list[str], source: dict, errors: list[str]) -> list[Item]:
    result: list[Item] = []
    if not urls:
        return result
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch, url, True, 1): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                item = parse_article(future.result(), url, source["name"])
                if item:
                    result.append(item)
            except Exception as exc:
                errors.append(f"{source['short_name']} article {url}: {exc}")
    return result


def collect_source(source: dict, start_day: date, end_day: date) -> tuple[list[Item], list[str]]:
    errors: list[str] = []
    gathered: list[Item] = []
    for feed_url in source.get("feeds", []):
        try:
            gathered.extend(parse_feed(fetch(feed_url), source["name"]))
        except Exception as exc:
            errors.append(f"{source['short_name']} feed {feed_url}: {exc}")

    feed_by_key = {canonical_key(item.link): item for item in gathered}
    seen_candidates: set[str] = set()
    fetched_candidates: set[str] = set()
    consecutive_listing_errors = 0
    paginated = bool(source.get("pagination"))
    for listing_url in listing_page_urls(source):
        try:
            page_urls, page_images = listing_candidates(fetch(listing_url, accept_html=True), listing_url, source)
            consecutive_listing_errors = 0
        except Exception as exc:
            errors.append(f"{source['short_name']} listing {listing_url}: {exc}")
            consecutive_listing_errors += 1
            if paginated and consecutive_listing_errors >= 2:
                break
            continue

        new_urls = [url for url in page_urls if canonical_key(url) not in seen_candidates]
        if not new_urls:
            if paginated:
                break
            continue
        seen_candidates.update(canonical_key(url) for url in new_urls)

        to_fetch = []
        for url in new_urls:
            key = canonical_key(url)
            feed_item = feed_by_key.get(key)
            image_hint = page_images.get(key, "")
            if feed_item is not None and image_hint and not feed_item.image:
                feed_item.image = image_hint
            hint_day = date_hint_from_url(url)
            if hint_day and hint_day < start_day:
                continue
            enrich_feed_items = source.get("enrich_feed_item_pages", True)
            if feed_item is None or enrich_feed_items and not feed_item.image:
                to_fetch.append(url)
                fetched_candidates.add(key)
        page_items = fetch_article_batch(to_fetch, source, errors)
        gathered.extend(page_items)

        page_dates = [item.published.astimezone(TZ).date() for item in page_items]
        page_dates.extend(hint for hint in (date_hint_from_url(url) for url in new_urls) if hint)
        page_dates.extend(
            feed_by_key[canonical_key(url)].published.astimezone(TZ).date()
            for url in new_urls
            if canonical_key(url) in feed_by_key
        )
        if paginated and page_dates:
            ordered_dates = sorted(page_dates)
            median_day = ordered_dates[len(ordered_dates) // 2]
            if median_day < start_day:
                break

    if source.get("enrich_feed_item_pages", True):
        missing_image_urls = [
            item.link
            for item in gathered
            if not item.image and canonical_key(item.link) not in fetched_candidates
        ]
        gathered.extend(fetch_article_batch(list(dict.fromkeys(missing_image_urls)), source, errors))

    dedup: dict[str, Item] = {}
    for item in sorted(gathered, key=lambda row: row.published, reverse=True):
        if in_edition_window(item, start_day, end_day):
            key = canonical_key(item.link)
            current = dedup.get(key)
            dedup[key] = item if current is None else richer_item(current, item)
    return sorted(dedup.values(), key=lambda row: row.published, reverse=True), errors


def canonical_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))


def fill_shared_images(items: list[Item]) -> list[Item]:
    """Reuse an image when the same article appears in another configured source."""
    images = {
        canonical_key(item.link): item.image
        for item in items
        if item.image
    }
    for item in items:
        if not item.image:
            item.image = images.get(canonical_key(item.link), "")
    return items


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
    return fill_shared_images(all_items), errors


def relative_age(published: datetime, now: datetime) -> str:
    seconds = max(0, int((now.astimezone(timezone.utc) - published.astimezone(timezone.utc)).total_seconds()))
    minutes = seconds // 60
    if minutes < 1:
        return "剛剛"
    if minutes < 60:
        return f"{minutes} 分鐘前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小時前"
    days, remaining_hours = divmod(hours, 24)
    if remaining_hours:
        return f"{days} 天 {remaining_hours} 小時前"
    return f"{days} 天前"


def story(item: Item, generated_at: datetime) -> str:
    stamp = item.published.astimezone(TZ).strftime("%Y.%m.%d %H:%M")
    machine_stamp = item.published.astimezone(TZ).isoformat()
    age = relative_age(item.published, generated_at)
    image = (
        f'<img src="{html.escape(item.image, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
        if item.image else '<div class="image-fallback" aria-hidden="true">NEWS</div>'
    )
    return f'''<article class="story">
  <a class="story-image" href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">{image}</a>
  <div class="story-copy">
    <time datetime="{html.escape(machine_stamp, quote=True)}" data-stamp="{stamp}">{age} · {stamp}</time>
    <h3><a href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(item.title)}</a></h3>
    <p>{html.escape(item.summary or DEFAULT_SUMMARY)}</p>
    <a class="read" href="{html.escape(item.link, quote=True)}" target="_blank" rel="noopener noreferrer">閱讀原文 →</a>
  </div>
</article>'''


def render(items: list[Item], sources: list[dict], start_day: date, end_day: date, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now(TZ)
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
            body = "\n".join(story(item, generated_at) for item in source_items)
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
.stories{{display:grid;gap:13px}}.story{{display:grid;grid-template-columns:260px minmax(0,1fr);align-items:start;background:var(--paper);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 7px 24px rgba(24,34,48,.05)}}
.story-image{{display:block;align-self:start;width:100%;aspect-ratio:16/10;background:linear-gradient(135deg,#ddd8ff,#ffd9cf);overflow:hidden}}.story-image img{{width:100%;height:100%;object-fit:cover;object-position:center;display:block}}.image-fallback{{display:grid;place-items:center;width:100%;height:100%;color:#7067a4;font-weight:900;letter-spacing:.14em}}
.story-copy{{padding:17px 20px}}time{{font-size:12px;color:var(--brand);font-weight:800}}h3{{font-size:20px;line-height:1.4;margin:5px 0 7px}}h3 a{{text-decoration:none}}h3 a:hover{{text-decoration:underline}}
.story p{{margin:0 0 8px;color:#4b5565}}.read{{font-size:13px;color:var(--brand);font-weight:800;text-decoration:none}}.empty{{padding:22px;background:#fff;border:1px dashed #c9c4bb;border-radius:12px;color:var(--muted)}}
footer{{background:#fff;border-top:1px solid var(--line);padding:26px 0 42px;color:var(--muted);font-size:13px}}
.back-to-top{{position:fixed;right:18px;bottom:18px;z-index:20;width:48px;height:48px;border:0;border-radius:50%;display:grid;place-items:center;background:#1e2442;color:#fff;font:800 26px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 8px 24px rgba(24,34,48,.3);cursor:pointer;opacity:0;visibility:hidden;pointer-events:none;transform:translateY(8px);transition:opacity .2s ease,transform .2s ease,visibility .2s ease}}
.back-to-top.is-visible{{opacity:1;visibility:visible;pointer-events:auto;transform:translateY(0)}}.back-to-top:hover{{background:var(--brand)}}.back-to-top:focus-visible{{outline:3px solid #ffd3c8;outline-offset:3px}}
@media(max-width:900px){{.story{{grid-template-columns:220px minmax(0,1fr)}}}}
@media(max-width:680px){{header{{padding:40px 0 32px}}.story{{grid-template-columns:140px minmax(0,1fr)}}.story-image{{aspect-ratio:4/3}}.story-copy{{padding:13px}}h3{{font-size:17px}}.story p{{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}}}
@media(max-width:480px){{.story{{grid-template-columns:1fr}}.story-image{{aspect-ratio:16/9}}.story-copy{{padding:15px}}.back-to-top{{right:14px;bottom:14px;width:44px;height:44px;font-size:24px}}}}
@media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.back-to-top{{transition:none}}}}
</style></head><body>
<header><div class="wrap"><div class="kicker">TWO-DAY TECHNOLOGY NEWSLETTER</div><h1>每兩日科技電子報</h1><div class="date">{date_range}</div>
<div class="stats"><div class="stat"><b>{len(sources)}</b>個來源</div><div class="stat"><b>{count}</b>篇文章</div><div class="stat">兩日自動翻頁</div></div>
<p class="source-line"><strong>本期來源：</strong>{html.escape(source_names)}</p></div></header>
<nav><div class="wrap">{''.join(nav)}</div></nav>
<main class="wrap">{''.join(sections)}</main>
<footer><div class="wrap">每日約 10:07（Asia/Taipei）更新，內容涵蓋今天與昨天；支援分頁的來源會自動往後讀取，直到文章早於本期日期範圍或沒有新文章才停止，並依網址去重。每篇同時顯示相對時間與台灣時間；摘要與圖片取自來源公開中繼資料，內容以原文為準。</div></footer>
<button class="back-to-top" id="back-to-top" type="button" aria-label="回到頁面頂端" title="回到頂端">↑</button>
<script>
function refreshRelativeTimes(){{
  const now=Date.now();
  document.querySelectorAll("time[datetime][data-stamp]").forEach((node)=>{{
    const seconds=Math.max(0,Math.floor((now-Date.parse(node.dateTime))/1000));
    const minutes=Math.floor(seconds/60);
    let age="剛剛";
    if(minutes>=1&&minutes<60) age=`${{minutes}} 分鐘前`;
    if(minutes>=60){{
      const hours=Math.floor(minutes/60);
      if(hours<24) age=`${{hours}} 小時前`;
      else{{
        const days=Math.floor(hours/24), remaining=hours%24;
        age=remaining?`${{days}} 天 ${{remaining}} 小時前`:`${{days}} 天前`;
      }}
    }}
    node.textContent=`${{age}} · ${{node.dataset.stamp}}`;
  }});
}}
refreshRelativeTimes();
setInterval(refreshRelativeTimes,60000);
const backToTop=document.getElementById("back-to-top");
function refreshBackToTop(){{
  backToTop.classList.toggle("is-visible",window.scrollY>320);
}}
backToTop.addEventListener("click",()=>{{
  const reduceMotion=window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({{top:0,behavior:reduceMotion?"auto":"smooth"}});
}});
window.addEventListener("scroll",refreshBackToTop,{{passive:true}});
refreshBackToTop();
</script>
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
    output = Path(args.output_dir)
    cache_path = output / "article-cache.json"
    cached_items = load_cache(cache_path, config["sources"])
    fresh_items, errors = collect(config, now)
    items = fill_shared_images(merge_items([fresh_items, cached_items], start_day, end_day))
    if not items:
        for message in errors:
            print(message, file=sys.stderr)
        print("No articles matched today or yesterday; refusing to overwrite the current edition.", file=sys.stderr)
        return 2
    (output / "archive").mkdir(parents=True, exist_ok=True)
    edition = end_day.strftime("%Y-%m-%d")
    content = render(items, config["sources"], start_day, end_day, now)
    (output / "index.html").write_text(content, encoding="utf-8")
    (output / "archive" / f"{edition}.html").write_text(content, encoding="utf-8")
    counts = {source["short_name"]: sum(1 for item in items if item.source == source["name"]) for source in config["sources"]}
    payload = {"date": edition, "start_date": start_day.isoformat(), "end_date": end_day.isoformat(), "source_count": 8, "count": len(items), "per_source": counts, "warnings": len(errors)}
    (output / "edition.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cache_start = end_day - timedelta(days=7)
    cache_items = merge_items([fresh_items, cached_items], cache_start, end_day)
    cache_payload = {"updated": now.astimezone(timezone.utc).isoformat(), "items": [item_to_dict(item) for item in cache_items]}
    cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for message in errors:
        print("WARNING:", message, file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

