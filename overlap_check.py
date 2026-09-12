"""
overlap_check.py
-----------------
Deliberately does NOT auto-decide "is there overlap" or "is the pool big
enough". Those calls need a person to look at actual study populations,
not just keyword matches -- a bot matching on keywords will regularly
misjudge both. A keyword hit count also can't tell whether those studies'
populations actually overlap with each other, or whether a PROSPERO record
is still active/withdrawn/completed.

What this DOES do: get real PubMed hit counts (via esearch, not just a
link) for the narrow PICO, the broadened PICO, and existing reviews, and
surface a plain-language "worth a closer look" / "probably too thin" /
"probably already covered" flag from those counts alone. That flag is a
triage nudge, not a go/no-go decision -- it still requires a human to open
the links and actually read the candidate studies before anything gets
registered.
"""

import os
import time
from typing import Dict
from urllib.parse import quote_plus

import requests

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")

# Rough triage thresholds -- tune these for your field. These are NOT a
# feasibility rule; they only decide which plain-language label gets shown.
THIN_POOL_MAX = 4          # <= this many broad-pool hits -> "probably too thin"
COVERED_REVIEW_MIN = 1     # >= this many existing reviews -> "check for overlap"


def _pubmed_count(query: str) -> int:
    """Return PubMed's total hit count for a search string (0 on failure)."""
    if not query:
        return 0
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": "0",
        "retmode": "json",
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    try:
        r = requests.get(f"{NCBI_BASE}/esearch.fcgi", params=params, timeout=30)
        r.raise_for_status()
        time.sleep(0.4)  # be polite to NCBI rate limits
        return int(r.json().get("esearchresult", {}).get("count", 0))
    except Exception as e:
        print(f"[warn] pubmed count failed for query '{query}': {e}")
        return 0


def build_review_links(pico: Dict) -> Dict:
    """Given a PICO dict, get real pool/overlap counts plus links for a human to check."""
    intervention = pico.get("intervention", "")
    outcome = pico.get("outcome", "")
    population = pico.get("population", "")

    keyword_query = " ".join(
        term for term in [population, intervention, outcome] if term and term != "unclear"
    ).strip()

    if not keyword_query:
        return {"error": "PICO too thin to build a meaningful search."}

    narrow_search = pico.get("narrow_pool_search", keyword_query)
    broad_search = pico.get("broad_pool_search", keyword_query)
    review_search = f"{keyword_query} AND (systematic review[pt] OR meta-analysis[pt])"

    narrow_count = _pubmed_count(narrow_search)
    broad_count = _pubmed_count(broad_search)
    review_count = _pubmed_count(review_search)

    if review_count >= COVERED_REVIEW_MIN:
        signal = (
            f"CHECK OVERLAP: {review_count} existing systematic review/meta-analysis "
            "hit(s) on this keyword set. Read them before assuming this is novel."
        )
    elif broad_count <= THIN_POOL_MAX:
        signal = (
            f"PROBABLY TOO THIN: only ~{broad_count} studies even on the broadened "
            "PICO. Likely not enough for meaningful pooling yet."
        )
    else:
        signal = (
            f"WORTH A CLOSER LOOK: ~{narrow_count} on the narrow PICO, ~{broad_count} "
            f"on the broadened PICO, {review_count} existing reviews found by keyword. "
            "Still open the links and read the candidates -- this is a keyword count, "
            "not a check of whether those populations actually overlap with each other."
        )

    prospero_url = (
        "https://www.crd.york.ac.uk/prospero/#recordsSubmitted?"
        f"searchType=1&RecordID=&titleSearch={quote_plus(keyword_query)}"
    )
    # Fallback: PROSPERO's own search UI is JS-heavy and this deep-link may
    # not always land exactly right -- if it doesn't, just search manually
    # at https://www.crd.york.ac.uk/prospero/ using the same keywords.

    pubmed_existing_reviews_url = "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(review_search)
    pubmed_narrow_pool_url = "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(narrow_search)
    pubmed_broad_pool_url = "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(broad_search)

    return {
        "narrow_pool_count": narrow_count,
        "broad_pool_count": broad_count,
        "existing_review_count": review_count,
        "signal": signal,
        "check_existing_registered_reviews": prospero_url,
        "check_existing_published_reviews": pubmed_existing_reviews_url,
        "estimate_narrow_pool": pubmed_narrow_pool_url,
        "estimate_broad_pool": pubmed_broad_pool_url,
        "human_review_required": (
            "These are keyword hit counts, not a verified pool. Open the links and "
            "confirm: (1) no other team already has this exact PICO registered/"
            "published recently, (2) the studies in the pool actually share a "
            "comparable population/intervention/outcome, not just shared keywords. "
            "Do this BEFORE registering on PROSPERO."
        ),
    }
