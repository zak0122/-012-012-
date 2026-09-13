"""
pico_extract.py
----------------
Drafts a PICO breakdown from an article's abstract using an LLM. Supports
two providers so you can run this at zero cost:

- Gemini (default if GEMINI_API_KEY is set): Google's free tier for
  Flash-class models covers this use case easily (a handful of calls a
  day), no credit card needed. Get a key at https://aistudio.google.com/apikey
- Anthropic/Claude (used if ANTHROPIC_API_KEY is set instead): NOT an
  ongoing free tier -- new accounts get a one-time ~$5 trial credit, then
  it's pay-per-token. Still very cheap for this volume, just not $0.

If both keys are present, GEMINI_API_KEY wins (set PICO_PROVIDER=anthropic
to force Claude instead).

IMPORTANT either way: this produces a DRAFT only. LLMs make mistakes
reading abstracts (wrong population, missed comparator, misread outcome
direction). Every PICO here must be checked against the actual abstract
by a person before it feeds into any registration (e.g. PROSPERO) or
write-up decision.
"""

import json
import os
import time
from typing import Dict

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
PROVIDER = os.environ.get("PICO_PROVIDER") or ("gemini" if GEMINI_API_KEY else "anthropic")

GEMINI_MODEL = "gemini-2.5-flash"  # free-tier eligible
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
CLAUDE_MODEL = "claude-sonnet-5"

PROMPT_TEMPLATE = """You are assisting a physician doing literature surveillance.
Read the abstract below and extract a draft PICO. Be conservative: if a field
is not clearly stated, say "unclear" rather than guessing.

Then ALSO draft a "broadened" version of the same PICO -- this is not a
different question, it is the same question generalized just enough that a
meta-analyst could plausibly pool studies under it. For example: widen a
narrow drug dose/formulation to the drug class, widen a single outcome
timepoint to the outcome family, widen an over-specific population (e.g.
"elderly Japanese men with HFrEF and eGFR<30") to the clinically meaningful
population (e.g. "adults with HFrEF and advanced CKD"). Do NOT broaden so far
that it becomes a different clinical question -- note in
"broadening_notes" what you widened and why, so a human can sanity check it.

Also suggest two short PubMed search strings (using MeSH-style terms where
sensible):
- "narrow_pool_search": estimates studies on the exact PICO as reported
- "broad_pool_search": estimates studies on the broadened PICO
These are only rough pool-size proxies, not real search strategies -- a
human still has to look at what comes back.

Return ONLY valid JSON, no markdown fences, no preamble, in this exact shape:
{{
  "population": "...",
  "intervention": "...",
  "comparator": "...",
  "outcome": "...",
  "study_design": "...",
  "confidence_notes": "any caveats about ambiguity in the abstract",
  "broadened_population": "...",
  "broadened_intervention": "...",
  "broadened_outcome": "...",
  "broadening_notes": "what was widened and why, so a human can sanity check it",
  "narrow_pool_search": "a PubMed search string",
  "broad_pool_search": "a PubMed search string"
}}

Title: {title}

Abstract: {abstract}
"""


def _clean_json_text(text: str) -> str:
    text = text.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    return text


def _draft_pico_gemini(prompt: str, max_retries: int = 3) -> Dict:
    params = {"key": GEMINI_API_KEY}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.2},
    }

    last_status = None
    for attempt in range(max_retries):
        try:
            r = requests.post(GEMINI_URL, params=params, json=body, timeout=60)
        except requests.RequestException as e:
            return {"error": f"Gemini request failed: {type(e).__name__}"}

        if r.status_code == 429:
            # Free-tier rate/quota limit -- back off and retry a couple of
            # times before giving up. Don't raise_for_status() here: that
            # would embed the full request URL (including our API key) in
            # the exception text, and that text can end up committed to the
            # output report. Never surface the URL/key anywhere.
            last_status = 429
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt * 5)  # 5s, 10s, 20s
                continue
            return {
                "error": (
                    "Gemini free-tier quota/rate limit exceeded (HTTP 429) "
                    "after retries. Check quota at https://aistudio.google.com/apikey, "
                    "or set PICO_PROVIDER=anthropic to use Claude instead."
                )
            }

        if not r.ok:
            return {"error": f"Gemini request failed with HTTP {r.status_code}."}

        data = r.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return {"error": "Gemini response had no usable text."}
        text = _clean_json_text(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Could not parse Gemini output as JSON."}

    return {"error": f"Gemini request failed (last status: {last_status})."}


def _draft_pico_anthropic(prompt: str) -> Dict:
    import anthropic  # imported lazily so Gemini-only setups don't need this package

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    text = _clean_json_text(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Could not parse Claude output as JSON.", "raw": text}


def draft_pico(title: str, abstract: str) -> Dict:
    if not abstract or len(abstract.strip()) < 40:
        return {
            "error": "Abstract too short or missing -- cannot draft PICO reliably.",
        }

    prompt = PROMPT_TEMPLATE.format(title=title, abstract=abstract)

    if PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            return {"error": "PICO_PROVIDER=gemini but GEMINI_API_KEY is not set."}
        result = _draft_pico_gemini(prompt)
        # If Gemini's free tier is out of quota for the day and Anthropic is
        # configured as a backup, fall back automatically instead of just
        # failing every article until the quota resets.
        if "error" in result and "429" in result.get("error", "") or "quota" in result.get("error", "").lower():
            if ANTHROPIC_API_KEY:
                fallback = _draft_pico_anthropic(prompt)
                if "error" not in fallback:
                    fallback["confidence_notes"] = (
                        fallback.get("confidence_notes", "") +
                        " [Note: drafted by Claude fallback -- Gemini quota was exhausted.]"
                    ).strip()
                return fallback
        return result
    elif PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            return {"error": "PICO_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."}
        return _draft_pico_anthropic(prompt)
    else:
        return {"error": f"Unknown PICO_PROVIDER '{PROVIDER}' -- use 'gemini' or 'anthropic'."}


if __name__ == "__main__":
    # quick manual test
    sample_title = "Example RCT of Drug X vs placebo in heart failure"
    sample_abstract = (
        "Background: Drug X has shown promise in small trials. Methods: We "
        "randomized 400 adults with HFrEF to Drug X or placebo for 12 months. "
        "Results: Drug X reduced hospitalization for heart failure (HR 0.78). "
        "Conclusion: Drug X reduces HF hospitalization in HFrEF patients."
    )
    print(f"Using provider: {PROVIDER}")
    print(json.dumps(draft_pico(sample_title, sample_abstract), indent=2))


