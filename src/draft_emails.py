"""Generate outreach email drafts using candidate profile and company data."""

import json
import os
import re
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(_root / ".env", override=True)
load_dotenv(Path.cwd() / ".env", override=True)

from src.models import CandidateProfile, CompanyDraft
from src.utils import ensure_dirs, log, read_csv, write_csv

PROMPT_PATH = _root / "configs" / "prompts" / "draft_email.txt"
CANDIDATE_PROFILE_PATH = _root / "data" / "processed" / "candidate_profile.json"
COMPANIES_PATH = _root / "data" / "processed" / "yc_companies_processed.csv"
COMPANIES_RAW_PATH = _root / "data" / "raw" / "yc_companies_raw.csv"
DRAFTS_OUTPUT_PATH = _root / "data" / "final" / "drafts.csv"
API_TIMEOUT_SEC = 60


def _load_candidate_profile() -> CandidateProfile:
    """Load candidate profile from JSON and return CandidateProfile."""
    path = Path(CANDIDATE_PROFILE_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Candidate profile not found: {path}. Run extract_resume.py first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CandidateProfile.model_validate(data)


def _format_company_info(row: dict) -> str:
    """Format a company row for the prompt."""
    parts = []
    if row.get("company_name"):
        parts.append(f"Name: {row['company_name']}")
    if row.get("yc_batch"):
        parts.append(f"Batch: {row['yc_batch']}")
    if row.get("location"):
        parts.append(f"Location: {row['location']}")
    if row.get("short_description"):
        parts.append(f"About: {row['short_description']}")
    if row.get("website_url"):
        parts.append(f"Website: {row['website_url']}")
    return "\n".join(parts) if parts else "No company details."


def _load_prompt_template() -> str:
    """Load draft email prompt template."""
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt not found: {PROMPT_PATH}")
    return PROMPT_PATH.read_text(encoding="utf-8")


def _strip_json_block(content: str) -> str:
    """Remove markdown code fence if present."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
    return content.strip()


def _call_openai(prompt: str) -> str:
    """Call OpenAI for draft; return response content."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("OPENAI_API_KEY is not set. Set it in your environment or .env file.")
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        timeout=API_TIMEOUT_SEC,
    )
    msg = response.choices[0].message
    if not msg or not msg.content:
        raise ValueError("Empty response from OpenAI")
    return msg.content


def _draft_for_company(
    template: str,
    candidate_profile: str,
    user_highlights: str,
    company_row: dict,
) -> CompanyDraft:
    """Build prompt and get one draft for a company."""
    company_info = _format_company_info(company_row)
    prompt = (
        template.replace("{candidate_profile}", candidate_profile)
        .replace("{user_highlights}", user_highlights)
        .replace("{company_info}", company_info)
    )
    content = _call_openai(prompt)
    content = _strip_json_block(content)
    data = json.loads(content)
    tier = company_row.get("tier", "") or ""
    return CompanyDraft(
        tier=tier,
        subject=data.get("subject", ""),
        body=data.get("body", ""),
    )


def main() -> None:
    """Load profile and companies, generate drafts, save to CSV."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate outreach email drafts. Optionally pass highlights to emphasize."
    )
    parser.add_argument(
        "--highlight",
        "-H",
        type=str,
        default="",
        help="What to emphasize: resume items to highlight and/or experience not on the resume (e.g. 'quant internship, Python automation; also mention leading fintech club').",
    )
    args = parser.parse_args()
    user_highlights = (args.highlight or "").strip()
    if not user_highlights:
        user_highlights = "None specified; use the candidate profile as usual."

    ensure_dirs()
    log("Loading candidate profile...")
    profile = _load_candidate_profile()
    compact = profile.compact_profile()
    log(f"Compact profile length: {len(compact)} chars")
    if user_highlights and user_highlights != "None specified; use the candidate profile as usual.":
        log(f"User highlights: {user_highlights[:80] + ('...' if len(user_highlights) > 80 else '')}")

    log("Loading prompt template...")
    template = _load_prompt_template()

    companies_path = COMPANIES_PATH if COMPANIES_PATH.exists() else COMPANIES_RAW_PATH
    if not companies_path.exists():
        raise FileNotFoundError(
            f"No company data at {COMPANIES_PATH} or {COMPANIES_RAW_PATH}. Run scrape_yc.py first."
        )
    log(f"Loading companies from {companies_path}...")
    rows = read_csv(companies_path)
    if not rows:
        log("No companies to draft for.")
        return

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError("OPENAI_API_KEY is not set. Set it in your environment or .env file.")

    drafts: list[dict] = []
    for i, row in enumerate(rows):
        log(f"Drafting [{i + 1}/{len(rows)}] {row.get('company_name', '?')}...")
        try:
            draft = _draft_for_company(template, compact, user_highlights, row)
            drafts.append({**row, "subject": draft.subject, "body": draft.body})
        except Exception as e:
            log(f"Failed for {row.get('company_name', '?')}: {e}")
            continue

    write_csv(DRAFTS_OUTPUT_PATH, drafts)
    log(f"Wrote {len(drafts)} drafts to {DRAFTS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
