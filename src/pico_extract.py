"""
pico_extract.py
----------------
Drafts a PICO breakdown from an article's abstract using an LLM.

Providers:
- Gemini (default if GEMINI_API_KEY is set): free tier, no credit card.
  Get a key at https://aistudio.google.com/apikey
- Anthropic/Claude (used if ANTHROPIC_API_KEY is set instead, or as an
  automatic fallback when Gemini fails): pay-per-token.

If both keys are present, Gemini is used first. Set PICO_PROVIDER=anthropic
to force Claude.

Optional env vars:
- GEMINI_MODEL   : override the Gemini model name without editing code
- CLAUDE_MODEL   : override the Claude model name without editing code

IMPORTANT: this produces a DRAFT only. Every PICO must be checked against
the actual abstract by a person before it feeds into any registration
(e.g. PROSPERO) or write-up decision.
"""

import json
import os
import time
from typing import Dict, List

import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
PROVIDER = os.environ.get("PICO_PROVIDER") or ("gemini" if GEMINI_API_KEY else "anthropic")

# Gemini models change often (older ones get shut down and return HTTP 404).
# The first one is tried first; if it returns 404 the next one is tried.
# You can also set the GEMINI_MODEL env var to put your own choice first.
GEMINI_MODELS: List[str] = [
    m for m in [
        os.environ.get("GEMINI_MODEL"),
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite-preview",
    ] if m
]
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5"

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
    """Strip markdown fences / a leading 'json' label, and trim to the outer {...}."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text


def _extract_gemini_text(data: Dict) -> str:
    """Join all normal text parts (skips 'thought' parts that newer models may return)."""
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _draft_pico_gemini(prompt: str, max_retries: int = 3) -> Dict:
    # NOTE: never put the API key in an error message. Errors here can end up
    # committed to the public output report.
    params = {"key": GEMINI_API_KEY}
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            # Newer Gemini models "think" first and thinking tokens count
            # toward this limit, so keep it generous or the JSON gets cut off.
            "maxOutputTokens": 4096,
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    last_error = "Gemini request failed."

    for model in GEMINI_MODELS:
        url = f"{GEMINI_BASE_URL}/{model}:generateContent"

        for attempt in range(max_retries):
            try:
                r = requests.post(url, params=params, json=body, timeout=90)
            except requests.RequestException as e:
                return {"error": f"Gemini request failed: {type(e).__name__}"}

            if r.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt * 5)  # 5s, 10s
                    continue
                return {
                    "error": (
                        "Gemini free-tier quota/rate limit exceeded (HTTP 429) "
                        "after retries. Check quota at https://aistudio.google.com/apikey, "
                        "or add ANTHROPIC_API_KEY to use Claude as a fallback."
                    )
                }

            if r.status_code == 404:
                # Model name retired or wrong -> try the next model in the list.
                last_error = (
                    f"Gemini model '{model}' returned HTTP 404 (model retired or "
                    "name wrong). Update GEMINI_MODELS in pico_extract.py."
                )
                break

            if not r.ok:
                return {"error": f"Gemini request failed with HTTP {r.status_code} (model {model})."}

            try:
                text = _extract_gemini_text(r.json())
            except (KeyError, IndexError, ValueError):
                return {"error": "Gemini response had no usable text (possibly blocked or empty)."}

            try:
                return json.loads(_clean_json_text(text))
            except json.JSONDecodeError:
                return {"error": "Could not parse Gemini output as JSON (output may be cut off)."}

    return {"error": last_error}


def _draft_pico_anthropic(prompt: str) -> Dict:
    import anthropic  # imported lazily so Gemini-only setups don't need this package

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        return {"error": f"Claude request failed: {type(e).__name__}"}

    text = "".join(block.text for block in msg.content if block.type == "text")
    try:
        return json.loads(_clean_json_text(text))
    except json.JSONDecodeError:
        return {"error": "Could not parse Claude output as JSON."}


def draft_pico(title: str, abstract: str) -> Dict:
    if not abstract or len(abstract.strip()) < 40:
        return {"error": "Abstract too short or missing -- cannot draft PICO reliably."}

    prompt = PROMPT_TEMPLATE.format(title=title, abstract=abstract)

    if PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            return {"error": "PICO_PROVIDER=gemini but GEMINI_API_KEY is not set."}

        result = _draft_pico_gemini(prompt)

        # If Gemini failed for ANY reason and a Claude key exists, fall back
        # automatically instead of failing every article.
        if "error" in result and ANTHROPIC_API_KEY:
            fallback = _draft_pico_anthropic(prompt)
            if "error" not in fallback:
                fallback["confidence_notes"] = (
                    fallback.get("confidence_notes", "")
                    + f" [Note: drafted by Claude fallback -- Gemini error: {result['error']}]"
                ).strip()
                return fallback
        return result

    elif PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            return {"error": "PICO_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set."}
        return _draft_pico_anthropic(prompt)

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
