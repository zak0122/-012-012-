"""
monitor.py
----------
Polls PubMed (via NCBI E-utilities) for newly indexed articles in a list
of configured journals, and returns only the ones not seen before
(tracked in state/seen_pmids.json).

This is the "journal watching" layer. It does NOT decide anything about
research quality -- it just tells you "these are new".
"""

import json
import os
import time
from pathlib import Path
from typing import List, Dict

import requests
import yaml

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")  # optional, raises rate limit 3->10 req/s

# Filters the primary feed to actual primary-study designs -- RCTs plus
# cohort/observational designs -- instead of every article type PubMed
# indexes for the journal (letters, editorials, corrections, comments).
# Adjust freely; this is just meant to cut noise, not to gatekeep designs
# out that you'd want to see.
#
# IMPORTANT LIMITATION: publication-type tags like "Randomized Controlled
# Trial"[pt] are assigned during PubMed/MEDLINE's full indexing pass,
# which commonly lags DAYS TO WEEKS behind when an article first appears
# (edat = entry date). Combining a short "last N days"[edat] window with
# a [pt] filter means genuinely-new RCTs often get missed simply because
# they haven't been tagged yet -- not because they don't exist. To reduce
# that gap we OR in a title/abstract text-word fallback ([tiab]), which is
# searchable immediately on entry, no indexing wait required.
PRIMARY_STUDY_FILTER = (
    '"Randomized Controlled Trial"[pt] OR "Controlled Clinical Trial"[pt] '
    'OR "Clinical Trial"[pt] OR "Multicenter Study"[pt] OR "Observational Study"[pt] '
    'OR "Comparative Study"[pt] OR "Cohort Studies"[mh] OR "Prospective Studies"[mh] '
    'OR "Retrospective Studies"[mh] '
    'OR "randomized"[tiab] OR "randomised"[tiab] OR "randomly assigned"[tiab] '
    'OR "cohort"[tiab] OR "prospective cohort"[tiab] OR "retrospective cohort"[tiab]'
)

# Because of the indexing lag described above, also widen the default
# lookback window -- 7 days is too tight to reliably catch [pt]-tagged
# records. This still only surfaces PMIDs not already in state/seen_pmids.json,
# so widening this does not create duplicate re-reporting.
DEFAULT_DAYS_BACK = 21

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "journals.yaml"
STATE_PATH = ROOT / "state" / "seen_pmids.json"
REVIEW_STATE_PATH = ROOT / "state" / "seen_review_pmids.json"


def _load_journals() -> List[Dict]:
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg["journals"]


def _load_seen(path: Path = STATE_PATH) -> set:
    if path.exists():
        with open(path, "r") as f:
            return set(json.load(f))
    return set()


def _save_seen(seen: set, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def _esearch(issn: str, days_back: int = 7, extra_filter: str = "") -> List[str]:
    """Return PMIDs for a journal published in the last `days_back` days.
    `extra_filter` can add e.g. a publication-type filter."""
    term = f'"{issn}"[issn] AND ("last {days_back} days"[edat])'
    if extra_filter:
        term += f" AND ({extra_filter})"
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": "100",
        "retmode": "json",
        "sort": "most recent",
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    r = requests.get(f"{NCBI_BASE}/esearch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])


def _efetch_abstracts(pmids: List[str]) -> Dict[str, Dict]:
    """Fetch title/abstract/journal/pubdate for a batch of PMIDs."""
    if not pmids:
        return {}
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    r = requests.get(f"{NCBI_BASE}/efetch.fcgi", params=params, timeout=30)
    r.raise_for_status()

    import xml.etree.ElementTree as ET
    root = ET.fromstring(r.text)
    results = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        title = article.findtext(".//ArticleTitle") or ""
        abstract_parts = article.findall(".//AbstractText")
        abstract = " ".join((a.text or "") for a in abstract_parts)
        journal = article.findtext(".//Journal/Title") or ""
        pubdate_year = article.findtext(".//JournalIssue/PubDate/Year") or ""
        pubdate_month = article.findtext(".//JournalIssue/PubDate/Month") or ""
        results[pmid] = {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "journal": journal,
            "pubdate": f"{pubdate_month} {pubdate_year}".strip(),
            "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return results


def find_new_articles(days_back: int = DEFAULT_DAYS_BACK) -> List[Dict]:
    """Main entry point: returns list of new RCT-type article dicts across all journals."""
    journals = _load_journals()
    seen = _load_seen(STATE_PATH)
    new_articles = []

    for j in journals:
        try:
            pmids = _esearch(j["issn"], days_back=days_back, extra_filter=PRIMARY_STUDY_FILTER)
        except Exception as e:
            print(f"[warn] esearch failed for {j['name']} ({j['issn']}): {e}")
            continue

        fresh = [p for p in pmids if p not in seen]
        if not fresh:
            time.sleep(0.4)  # be polite to NCBI rate limits
            continue

        details = _efetch_abstracts(fresh)
        for pmid, d in details.items():
            d["journal_config_name"] = j["name"]
            new_articles.append(d)
            seen.add(pmid)

        time.sleep(0.4)

    _save_seen(seen, STATE_PATH)
    return new_articles


def find_new_reviews(days_back: int = DEFAULT_DAYS_BACK) -> List[Dict]:
    """Scan the same configured journals for newly indexed systematic
    reviews / meta-analyses -- this is separate from the RCT feed above so
    your team notices early if someone else publishes a review that
    overlaps with a topic you're working on or considering. It is still
    just a heads-up: read the actual article before concluding it overlaps
    with your PICO."""
    journals = _load_journals()
    seen = _load_seen(REVIEW_STATE_PATH)
    new_reviews = []
    review_filter = "systematic review[pt] OR meta-analysis[pt]"

    for j in journals:
        try:
            pmids = _esearch(j["issn"], days_back=days_back, extra_filter=review_filter)
        except Exception as e:
            print(f"[warn] review esearch failed for {j['name']} ({j['issn']}): {e}")
            continue

        fresh = [p for p in pmids if p not in seen]
        if not fresh:
            time.sleep(0.4)
            continue

        details = _efetch_abstracts(fresh)
        for pmid, d in details.items():
            d["journal_config_name"] = j["name"]
            new_reviews.append(d)
            seen.add(pmid)

        time.sleep(0.4)

    _save_seen(seen, REVIEW_STATE_PATH)
    return new_reviews


if __name__ == "__main__":
    articles = find_new_articles()
    print(f"Found {len(articles)} new RCT-type article(s).")
    for a in articles:
        print(f"- [{a['journal_config_name']}] {a['title']} ({a['link']})")

    reviews = find_new_reviews()
    print(f"Found {len(reviews)} new review/meta-analysis article(s).")
    for r in reviews:
        print(f"- [{r['journal_config_name']}] {r['title']} ({r['link']})")

