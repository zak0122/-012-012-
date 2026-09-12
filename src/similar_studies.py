"""
similar_studies.py
-------------------
Free "what else is out there on this?" check using Semantic Scholar's
public Graph API -- no API key, no per-call cost, generous enough free
rate limit for this use case (a handful of lookups a day).

This is NOT a replacement for the PubMed pool/overlap counts in
overlap_check.py -- those are the actual PICO-pool numbers. This is an
extra, broader net: Semantic Scholar indexes preprints and some
non-PubMed-indexed venues too, so it can occasionally surface a
relevant paper the PubMed searches miss. Like everything else here, a
human still has to read what comes back -- title-level similarity is
not the same as a shared PICO.
"""

import time
from typing import Dict, List

import requests

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


def find_similar_papers(title: str, limit: int = 5) -> List[Dict]:
    """Return up to `limit` Semantic Scholar papers similar to `title`."""
    if not title:
        return []
    params = {
        "query": title,
        "limit": limit,
        "fields": "title,year,venue,url,externalIds",
    }
    try:
        r = requests.get(S2_SEARCH_URL, params=params, timeout=20)
        if r.status_code == 429:
            # Free tier is rate-limited (not zero limit, just zero cost) --
            # back off once and retry rather than erroring the whole run.
            time.sleep(3)
            r = requests.get(S2_SEARCH_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json().get("data", [])
    except Exception as e:
        print(f"[warn] semantic scholar lookup failed for '{title}': {e}")
        return []

    results = []
    for p in data:
        # Skip the paper matching itself if the exact title comes back first.
        if p.get("title", "").strip().lower() == title.strip().lower():
            continue
        results.append(
            {
                "title": p.get("title"),
                "year": p.get("year"),
                "venue": p.get("venue"),
                "url": p.get("url"),
            }
        )
    return results


if __name__ == "__main__":
    for p in find_similar_papers("SGLT2 inhibitors and heart failure hospitalization"):
        print(f"- {p['title']} ({p['year']}, {p['venue']}) {p['url']}")
