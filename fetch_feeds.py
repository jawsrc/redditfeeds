#!/usr/bin/env python3
"""Turn subreddit JSON listings into Atom feeds. No API key, stdlib only.

Run on a schedule (see .github/workflows/update-feeds.yml). Feeds are written
to docs/feeds/ so GitHub Pages can serve them. A rolling history is kept in
state/ so posts don't vanish from a feed when they fall off Reddit's first page.
"""
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "feeds.json"
FEED_DIR = ROOT / "docs" / "feeds"
INDEX_PATH = ROOT / "docs" / "index.html"
STATE_DIR = ROOT / "state"

ATOM_NS = "http://www.w3.org/2005/Atom"
DEFAULT_UA = "script:reddit-atom-feeds:0.1 (personal feed generator)"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def retry_delay(err, attempt):
    header = err.headers.get("Retry-After", "") if err.headers else ""
    if header.isdigit():
        return min(int(header), 120)
    return 15 * attempt


def fetch_json(url, user_agent, retries=3):
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "application/json"}
    )
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = retry_delay(err, attempt)
                print(f"  HTTP {err.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            hint = " (Reddit is probably blocking this IP)" if err.code in (403, 429) else ""
            raise RuntimeError(f"HTTP {err.code}{hint}") from err
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            if attempt < retries:
                time.sleep(10 * attempt)
                continue
            raise RuntimeError(f"request failed or response was not JSON: {err}") from err


def feed_id(feed):
    sort = feed.get("sort", "new")
    parts = [feed["subreddit"], sort]
    if sort in ("top", "controversial"):
        parts.append(feed.get("time", "day"))
    return re.sub(r"[^a-z0-9_-]+", "-", "_".join(parts).lower())


def listing_url(feed):
    sort = feed.get("sort", "new")
    params = {"limit": feed.get("limit", 25), "raw_json": 1}
    if sort in ("top", "controversial"):
        params["t"] = feed.get("time", "day")
    return f"https://www.reddit.com/r/{feed['subreddit']}/{sort}.json?" + urllib.parse.urlencode(params)


# --------------------------------------------------------------------------
# Converting posts to feed items
# --------------------------------------------------------------------------

def edit_time(post):
    edited = post.get("edited")
    if isinstance(edited, (int, float)) and not isinstance(edited, bool) and edited:
        return int(edited)
    return 0


def build_content(post, permalink):
    parts = []
    if post.get("selftext_html"):
        parts.append(post["selftext_html"])
    if not post.get("is_self"):
        url = post.get("url_overridden_by_dest") or post.get("url") or ""
        if url.lower().split("?")[0].endswith(IMAGE_EXTS):
            parts.append(f'<p><img src="{escape(url)}" style="max-width:100%"></p>')
        else:
            thumb = post.get("thumbnail") or ""
            if thumb.startswith("http"):
                parts.append(f'<p><img src="{escape(thumb)}"></p>')
            if url:
                parts.append(f'<p><a href="{escape(url)}">[link]</a></p>')
    parts.append(f'<p><a href="{escape(permalink)}">[comments]</a></p>')
    return "\n".join(parts)


def to_item(post):
    permalink = "https://www.reddit.com" + post["permalink"]
    created = int(post["created_utc"])
    edited = edit_time(post)
    return {
        "id": permalink,
        "title": post.get("title", "(untitled)"),
        "author": post.get("author", "[deleted]"),
        "created": created,
        "edited": edited,
        "updated": edited or created,
        "content": build_content(post, permalink),
    }


def merge(old_items, new_items, max_items):
    """Keep history; only replace a stored item when the post was edited."""
    by_id = {item["id"]: item for item in old_items}
    for item in new_items:
        previous = by_id.get(item["id"])
        if previous is None or previous.get("edited") != item["edited"]:
            by_id[item["id"]] = item
    items = sorted(by_id.values(), key=lambda i: (i["created"], i["id"]), reverse=True)
    return items[:max_items]


# --------------------------------------------------------------------------
# Writing output
# --------------------------------------------------------------------------

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atom(path, feed, items, base_url):
    ET.register_namespace("", ATOM_NS)
    fid = feed_id(feed)
    sub, sort = feed["subreddit"], feed.get("sort", "new")

    def add(parent, tag, text=None, **attrs):
        el = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}", attrs)
        if text is not None:
            el.text = BAD_XML_CHARS.sub("", text)
        return el

    root = ET.Element(f"{{{ATOM_NS}}}feed")
    add(root, "title", feed.get("title") or f"r/{sub} ({sort})")
    add(root, "id", f"https://www.reddit.com/r/{sub}/{sort}/")
    add(root, "link", href=f"https://www.reddit.com/r/{sub}/{sort}/", rel="alternate")
    if base_url:
        add(root, "link", href=f"{base_url}/feeds/{fid}.xml", rel="self")
    # Derived from the items (not "now") so unchanged feeds produce identical files.
    add(root, "updated", iso(max((i["updated"] for i in items), default=0)))

    for item in items:
        entry = add(root, "entry")
        add(entry, "id", item["id"])
        add(entry, "title", item["title"])
        add(entry, "link", href=item["id"], rel="alternate")
        add(entry, "published", iso(item["created"]))
        add(entry, "updated", iso(item["updated"]))
        author = add(entry, "author")
        add(author, "name", f"/u/{item['author']}")
        add(entry, "content", item["content"], type="html")

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_index(feeds, base_url):
    rows = []
    for feed in feeds:
        fid = feed_id(feed)
        label = feed.get("title") or f"r/{feed['subreddit']} ({feed.get('sort', 'new')})"
        rows.append(f'<li><a href="feeds/{escape(fid)}.xml">{escape(label)}</a></li>')
    INDEX_PATH.write_text(
        "<!doctype html><meta charset=utf-8><title>Reddit feeds</title>"
        "<h1>Reddit feeds</h1><ul>" + "".join(rows) + "</ul>\n",
        encoding="utf-8",
    )


def default_base_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}"
    return ""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    user_agent = os.environ.get("REDDIT_USER_AGENT") or config.get("user_agent") or DEFAULT_UA
    base_url = (os.environ.get("FEED_BASE_URL") or default_base_url()).rstrip("/")
    delay = config.get("delay_seconds", 5)
    default_max = config.get("max_items_per_feed", 100)

    FEED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(exist_ok=True)

    succeeded, failed = [], []
    for n, feed in enumerate(config["feeds"]):
        fid = feed_id(feed)
        if n:
            time.sleep(delay + random.random() * 2)  # be polite between requests
        try:
            listing = fetch_json(listing_url(feed), user_agent)
            min_score = feed.get("min_score", float("-inf"))
            new_items = [
                to_item(child["data"])
                for child in listing["data"]["children"]
                if child.get("kind") == "t3" and child["data"].get("score", 0) >= min_score
            ]
        except Exception as err:  # keep going; old feed file stays as it was
            prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else ""
            print(f"{prefix}FAILED {fid}: {err}", file=sys.stderr)
            failed.append(fid)
            continue

        state_path = STATE_DIR / f"{fid}.json"
        old_items = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else []
        items = merge(old_items, new_items, feed.get("max_items", default_max))
        state_path.write_text(
            json.dumps(items, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        write_atom(FEED_DIR / f"{fid}.xml", feed, items, base_url)
        succeeded.append(fid)
        print(f"ok {fid}: {len(new_items)} fetched, {len(items)} in feed")

    write_index(config["feeds"], base_url)

    if failed and not succeeded:
        sys.exit(1)  # everything failed: make the workflow run show as failed


if __name__ == "__main__":
    main()
