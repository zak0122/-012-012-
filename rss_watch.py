"""
rss_watch.py
------------
Free, zero-API-key way to catch articles BEFORE they're indexed on
PubMed: many journals publish an "ahead of print" / "online first" RSS
feed on their own website that updates as soon as the journal itself
posts the article -- this is usually days before PubMed's own indexing
catches up (which is the real bottleneck for monitor.py's esearch-based
feed).

This only works for journals that (a) actually publish such a feed and
(b) put a usable title/summary in it. Add the feed URL under `rss:` for
a journal in config/journals.yaml to turn this on for it; journals with
no `rss:` key are simply skipped here (they still get covered once
PubMed indexes them, via monitor.py).

NOTE: RSS entries don't come with a PMID, and often don't come with a
full abstract -- so the PICO draft built from an RSS-only entry is
lower-confidence than one built from a PubMed abstract. Treat these as
"heads up, something new is coming" rather than a finished PICO source.
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "journals.yaml"
SEEN_RSS_PATH = ROOT / "state" / "seen_rss_links.json"


def _load_journals() -> List[Dict]:
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg["journals"]


def _load_seen() -> set:
    if SEEN_RSS_PATH.exists():
        with open(SEEN_RSS_PATH, "r") as f:
            return set(json.load(f))
    return set()


def _save_seen(seen: set) -> None:
    SEEN_RSS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_RSS_PATH, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def _entry_id(entry) -> str:
    """RSS entries don't have a PMID -- hash the link (or title as a
    fallback) so we can dedupe across runs."""
    raw = getattr(entry, "link", None) or getattr(entry, "title", "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def find_new_rss_items(max_per_feed: int = 20) -> List[Dict]:
    """Check every journal with an `rss:` feed configured; return entries
    not seen on a previous run."""
    journals = [j for j in _load_journals() if j.get("rss")]
    if not journals:
        return []

    seen = _load_seen()
    new_items = []

    for j in journals:
        try:
            feed = feedparser.parse(j["rss"])
        except Exception as e:
            print(f"[warn] rss parse failed for {j['name']} ({j['rss']}): {e}")
            continue

        if getattr(feed, "bozo", False) and not getattr(feed, "entries", []):
            print(f"[warn] rss feed looked broken for {j['name']}: {getattr(feed, 'bozo_exception', '')}")
            continue

        for entry in feed.entries[:max_per_feed]:
            eid = _entry_id(entry)
            if eid in seen:
                continue
            seen.add(eid)
            new_items.append(
                {
                    "title": getattr(entry, "title", "(untitled)"),
                    "link": getattr(entry, "link", ""),
                    "summary": getattr(entry, "summary", ""),
                    "pubdate": getattr(entry, "published", "") or getattr(entry, "updated", ""),
                    "journal_config_name": j["name"],
                }
            )

    _save_seen(seen)
    return new_items


if __name__ == "__main__":
    items = find_new_rss_items()
    print(f"Found {len(items)} new ahead-of-print RSS item(s).")
    for it in items:
        print(f"- [{it['journal_config_name']}] {it['title']} ({it['link']})")
