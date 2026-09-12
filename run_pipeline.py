"""
run_pipeline.py
----------------
Daily entry point (called by GitHub Actions):
  1. Find new articles across configured journals (monitor.py)
  2. Draft a PICO for each (pico_extract.py)
  3. Build human-review links for pool/overlap checks (overlap_check.py)
  4. Write a single markdown report to output/report_<date>.md

Nothing here auto-registers anything or auto-decides feasibility.
The report is meant to be read by your team each morning.
"""

import datetime as dt
from pathlib import Path

from monitor import find_new_articles, find_new_reviews
from pico_extract import draft_pico
from overlap_check import build_review_links
from rss_watch import find_new_rss_items
from similar_studies import find_similar_papers

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"


def build_report() -> str:
    articles = find_new_articles(days_back=7)
    reviews = find_new_reviews(days_back=7)
    rss_items = find_new_rss_items()
    today = dt.date.today().isoformat()

    lines = [f"# Journal Watch Report -- {today}"]

    if rss_items:
        lines.append(
            f"\n## 🕐 {len(rss_items)} ahead-of-print item(s) (not yet in PubMed)\n"
        )
        lines.append(
            "_From each journal's own RSS feed -- usually appears before PubMed "
            "indexing catches up. No PMID yet and often no full abstract, so "
            "treat these as an early heads-up, not a finished PICO source._\n"
        )
        for it in rss_items:
            lines.append(f"- **{it['title']}** -- {it['journal_config_name']}, {it['pubdate']} -- {it['link']}")
        lines.append("")

    if reviews:
        lines.append(
            f"\n## ⚠ {len(reviews)} new systematic review/meta-analysis published in your journals\n"
        )
        lines.append(
            "_Heads up only -- read these before assuming they overlap with a topic "
            "you're tracking or considering. A shared journal/keyword doesn't mean a "
            "shared population._\n"
        )
        for r in reviews:
            lines.append(f"- **{r['title']}** -- {r['journal_config_name']}, {r['pubdate']} -- {r['link']}")
        lines.append("")

    if not articles:
        lines.append("No new RCT-type articles found today.\n")
        return "\n".join(lines)

    lines.append(f"\n{len(articles)} new RCT-type article(s) found.\n")

    for a in articles:
        lines.append(f"## {a['title']}")
        lines.append(f"**Journal:** {a['journal_config_name']}  ")
        lines.append(f"**PubMed:** {a['link']}  ")
        lines.append(f"**Published:** {a['pubdate']}\n")

        pico = draft_pico(a["title"], a["abstract"])
        if "error" in pico:
            lines.append(f"_PICO draft failed: {pico['error']}_\n")
            lines.append("---\n")
            continue

        lines.append("**Draft PICO (unverified -- confirm against abstract):**")
        lines.append(f"- Population: {pico.get('population')}")
        lines.append(f"- Intervention: {pico.get('intervention')}")
        lines.append(f"- Comparator: {pico.get('comparator')}")
        lines.append(f"- Outcome: {pico.get('outcome')}")
        lines.append(f"- Study design: {pico.get('study_design')}")
        if pico.get("confidence_notes"):
            lines.append(f"- Notes: {pico.get('confidence_notes')}")

        if pico.get("broadened_population") or pico.get("broadened_intervention"):
            lines.append("\n**Broadened PICO (for pooling purposes -- sanity check this):**")
            lines.append(f"- Population: {pico.get('broadened_population')}")
            lines.append(f"- Intervention: {pico.get('broadened_intervention')}")
            lines.append(f"- Outcome: {pico.get('broadened_outcome')}")
            if pico.get("broadening_notes"):
                lines.append(f"- Why widened: {pico.get('broadening_notes')}")

        links = build_review_links(pico)
        if "error" not in links:
            lines.append(f"\n**Signal:** {links['signal']}")
            lines.append("\n**Pool counts (keyword-based, not verified):**")
            lines.append(f"- Narrow PICO pool: ~{links['narrow_pool_count']}")
            lines.append(f"- Broadened PICO pool: ~{links['broad_pool_count']}")
            lines.append(f"- Existing reviews found: {links['existing_review_count']}")
            lines.append("\n**Before doing anything else, check:**")
            lines.append(f"- Existing registered reviews: {links['check_existing_registered_reviews']}")
            lines.append(f"- Existing published reviews: {links['check_existing_published_reviews']}")
            lines.append(f"- Narrow pool search: {links['estimate_narrow_pool']}")
            lines.append(f"- Broadened pool search: {links['estimate_broad_pool']}")
            lines.append(f"\n_{links['human_review_required']}_")

        similar = find_similar_papers(a["title"])
        if similar:
            lines.append("\n**Also similar, via Semantic Scholar (free, broader net -- title-level only):**")
            for s in similar:
                lines.append(f"- {s['title']} ({s.get('year', '?')}, {s.get('venue', '?')}) -- {s.get('url', '')}")

        lines.append("\n---\n")

    return "\n".join(lines)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()
    out_path = OUTPUT_DIR / f"report_{dt.date.today().isoformat()}.md"
    out_path.write_text(report)
    print(f"Wrote report to {out_path}")
