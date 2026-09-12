# Journal Watch

Runs in the cloud (GitHub Actions, free tier is enough for this) and every
6 hours:

1. Checks each journal's own RSS feed (if configured) for ahead-of-print
   items PubMed hasn't indexed yet (`src/rss_watch.py`) -- free, no API key
2. Checks your configured journals on PubMed for anything newly indexed,
   filtered to RCT/cohort/observational study types (`src/monitor.py`)
3. Separately checks the same journals for newly indexed systematic
   reviews/meta-analyses, so you notice early if someone else publishes
   something close to a topic you're tracking (`src/monitor.py`)
4. Drafts a narrow + broadened PICO for each new article using an LLM --
   Gemini by default, free (`src/pico_extract.py`)
5. Gets real PubMed hit counts for the pool/overlap check, not just links
   (`src/overlap_check.py`)
6. Runs a free, no-key similar-papers lookup via Semantic Scholar as an
   extra net beyond PubMed keyword matching (`src/similar_studies.py`)
7. Writes it all to a markdown report in `output/` that your team reads

## Cost

Free with the default setup:
- **Gemini** (`GEMINI_API_KEY`) -- Google's ongoing free tier for Flash
  models easily covers this (daily limits like 500-1500 requests/day are
  far more than a few new articles a day needs), no credit card required.
  Get a key at https://aistudio.google.com/apikey. Free-tier prompts may
  be used by Google to improve their products -- worth knowing, though
  PubMed abstracts are already public.
- Everything else (GitHub Actions, PubMed E-utilities, RSS, Semantic
  Scholar) has no per-call cost regardless of provider.

If you'd rather use Claude instead: set `PICO_PROVIDER=anthropic` and add
`ANTHROPIC_API_KEY`. This is NOT an ongoing free tier -- new accounts get
a one-time ~$5 trial credit, then it's pay-per-token (still cheap for
this volume, a few cents/day at most, just not $0).

## What this tool does NOT do (on purpose)

- It does **not** decide whether the evidence pool is "enough" to justify a
  meta-analysis. That depends on heterogeneity, study quality, and power --
  not just a study count. A senior team member / biostatistician should
  make that call.
- It does **not** decide whether there's overlapping population with an
  existing review. Keyword matching can miss real overlap and can also
  flag false overlap. A person needs to actually read the candidate
  studies.
- It does **not** register anything on PROSPERO or submit anything
  anywhere. It only gets you to the "a person can decide in 5 minutes"
  stage instead of the "search PubMed manually every day" stage.

Skipping these human checks to move faster is exactly how low-quality,
rushed meta-analyses end up published and later criticized or retracted --
the speed advantage isn't worth it if the review doesn't hold up.

## Setup

1. **Fork/create a new GitHub repo** and push these files to it.
2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `GEMINI_API_KEY` -- free, from https://aistudio.google.com/apikey
   - `ANTHROPIC_API_KEY` -- optional, only needed if you set
     `PICO_PROVIDER=anthropic` to use Claude instead (not free)
   - `NCBI_API_KEY` -- optional but recommended, free from an NCBI account
     (raises your rate limit from 3 to 10 requests/sec)
3. Edit `config/journals.yaml` to add/remove journals. ISSNs matter more
   than names for the search to work -- double check them against
   [NLM Catalog](https://www.ncbi.nlm.nih.gov/nlmcatalog) if a journal
   isn't returning results. Optionally add an `rss:` feed URL per journal
   to catch ahead-of-print items before PubMed indexes them -- check the
   journal's own site for its RSS link, and verify the URL actually
   returns entries before relying on it.
4. The workflow in `.github/workflows/daily_monitor.yml` runs daily at
   06:00 UTC and commits the report back into `output/`. Adjust the cron
   schedule for your team's timezone, or trigger it manually from the
   Actions tab to test it (`workflow_dispatch`).
5. First run will have no `state/seen_pmids.json` yet -- it'll be created
   automatically. **The first run will surface everything from the last 7
   days as "new"** -- that's expected, subsequent runs only show truly new
   articles.

## Local test run

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your-key-here
cd src
python run_pipeline.py
cat ../output/report_*.md
```

## Extending

- Swap the "commit report to repo" step for a Slack webhook or email step
  if your team prefers notifications over checking the repo.
- Add a Google Sheet append step so the team has a running log with
  columns for "reviewed by", "decision", "PROSPERO ID" -- keeps the human
  decision auditable, which reviewers/editors increasingly expect anyway.
