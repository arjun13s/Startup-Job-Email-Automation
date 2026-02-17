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

USE_GPT_REFINEMENT = False

PROMPT_PATH = _root / "configs" / "prompts" / "draft_email.txt"
TEMPLATES_DIR = _root / "configs" / "templates"
CANDIDATE_PROFILE_PATH = _root / "data" / "processed" / "candidate_profile.json"
COMPANIES_PATH = _root / "data" / "processed" / "yc_companies_processed.csv"
COMPANIES_RAW_PATH = _root / "data" / "raw" / "yc_companies_raw.csv"
DRAFTS_OUTPUT_PATH = _root / "data" / "final" / "drafts.csv"
API_TIMEOUT_SEC = 60
MAX_FILLED_LENGTH = 2000


def _load_candidate_profile() -> CandidateProfile:
    """Load candidate profile from JSON and return CandidateProfile."""
    path = Path(CANDIDATE_PROFILE_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Candidate profile not found: {path}. Run extract_resume.py first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CandidateProfile.model_validate(data)


def load_template(tier: str) -> str:
    """Load template for tier from configs/templates/{tier}.txt. Returns empty string if missing."""
    if not tier or not tier.strip():
        tier = "standard"
    tier = re.sub(r"[^a-z0-9_]", "", tier.strip().lower()) or "standard"
    path = TEMPLATES_DIR / f"{tier}.txt"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def fill_template(
    template: str,
    company: dict,
    profile: CandidateProfile,
    score: dict,
) -> str:
    """Replace placeholders and clean output. Missing data -> empty string."""
    company_name = (company.get("company_name") or "").strip()
    company_description = (company.get("short_description") or company.get("company_description") or "").strip()
    jobs_url = (company.get("jobs_url") or "").strip()
    location = (company.get("location") or "").strip()

    candidate_name = (profile.name or "").strip()
    candidate_headline = (profile.headline or "").strip()
    candidate_core_skills = ", ".join(profile.core_skills) if profile.core_skills else ""
    candidate_quant_projects = ", ".join((profile.quant_projects or [])[:2])
    candidate_metrics = ", ".join((profile.metrics or [])[:2])
    solid_reasons = score.get("solid_reasons") if isinstance(score.get("solid_reasons"), list) else []
    custom_reference = (solid_reasons[0] if solid_reasons else "").strip()

    replacements = {
        "{{company_name}}": company_name,
        "{{company_description}}": company_description,
        "{{jobs_url}}": jobs_url,
        "{{location}}": location,
        "{{candidate_name}}": candidate_name,
        "{{candidate_headline}}": candidate_headline,
        "{{candidate_core_skills}}": candidate_core_skills,
        "{{candidate_quant_projects}}": candidate_quant_projects,
        "{{candidate_metrics}}": candidate_metrics,
        "{{custom_reference}}": custom_reference,
    }
    out = template
    for placeholder, value in replacements.items():
        out = out.replace(placeholder, value or "")

    # Remove duplicate blank lines, trim lines and overall
    lines = [line.rstrip() for line in out.splitlines()]
    cleaned_lines = []
    prev_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        cleaned_lines.append(line)
        prev_blank = is_blank
    out = "\n".join(cleaned_lines).strip()
    if len(out) > MAX_FILLED_LENGTH:
        out = out[:MAX_FILLED_LENGTH].rstrip()
    return out


def _extract_subject_from_body(text: str) -> tuple[str, str]:
    """If first line is 'Subject: ...', return (subject, body_without_subject_line). Else return ('', text)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower().startswith("subject:"):
            subject = stripped[8:].strip()
            body = "\n".join(lines[i + 1 :]).strip()
            return subject, body
    return "", text


def _gpt_refine_body(body: str) -> str:
    """Optional GPT refinement: improve clarity, keep structure, do not expand length."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return body
    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Improve the clarity of this email. Keep the same structure and length. "
                        "Do not remove or change content that was already filled in. "
                        "Return only the improved email body, no explanation.\n\n" + body
                    ),
                }
            ],
            temperature=0.2,
            timeout=API_TIMEOUT_SEC,
        )
        msg = response.choices[0].message
        if msg and msg.content:
            refined = msg.content.strip()
            if len(refined) <= len(body) * 1.1:
                return refined[:MAX_FILLED_LENGTH]
    except Exception:
        pass
    return body


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
        tier = (row.get("tier") or "standard").strip() or "standard"
        log(f"Drafting [{i + 1}/{len(rows)}] {row.get('company_name', '?')}...")
        try:
            template_text = load_template(tier)
            if template_text:
                print(f"[TEMPLATE MODE] Using template for tier: {tier}")
                score = {"solid_reasons": row.get("solid_reasons") if isinstance(row.get("solid_reasons"), list) else []}
                filled = fill_template(template_text, row, profile, score)
                subject, body = _extract_subject_from_body(filled)
                if not subject:
                    subject = f"{profile.name or 'Candidate'} – interested in {row.get('company_name', 'Company')}"
                if USE_GPT_REFINEMENT:
                    body = _gpt_refine_body(body)
                draft = CompanyDraft(tier=tier, subject=subject, body=body)
            else:
                log(f"No template for tier '{tier}', using GPT generation.")
                draft = _draft_for_company(template, compact, user_highlights, row)
            drafts.append({**row, "subject": draft.subject, "body": draft.body})
        except Exception as e:
            log(f"Failed for {row.get('company_name', '?')}: {e}")
            continue

    write_csv(DRAFTS_OUTPUT_PATH, drafts)
    log(f"Wrote {len(drafts)} drafts to {DRAFTS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
