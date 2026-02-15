"""Extract structured candidate profile from a resume file via OpenAI."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Ensure project root is on path when run as python src/extract_resume.py
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

# Load .env from project root and from current working directory (override so .env wins over empty env vars)
load_dotenv(_root / ".env", override=True)
load_dotenv(Path.cwd() / ".env", override=True)

from src.models import CandidateProfile
from src.utils import ensure_dirs, clean_text, log

MAX_RESUME_CHARS = 15_000
PROMPT_PATH = _root / "configs" / "prompts" / "extract_resume.txt"
OUTPUT_PATH = _root / "data" / "processed" / "candidate_profile.json"
API_TIMEOUT_SEC = 60


def _read_pdf(path: Path) -> str:
    """Extract text from PDF using pdfplumber."""
    text_parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            content = page.extract_text()
            if content:
                text_parts.append(content)
    return "\n".join(text_parts)


def _read_txt(path: Path) -> str:
    """Read plain text file."""
    return path.read_text(encoding="utf-8", errors="replace")


def load_resume_text(path: Path) -> str:
    """Load and clean resume text from .pdf or .txt."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Resume file not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw = _read_pdf(path)
    elif suffix == ".txt":
        raw = _read_txt(path)
    else:
        raise ValueError(f"Unsupported format: {suffix}. Use .pdf or .txt")

    cleaned = " ".join(raw.split())
    return clean_text(cleaned, MAX_RESUME_CHARS)


def load_prompt(resume_text: str) -> str:
    """Load prompt template and substitute resume text."""
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_PATH}")
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{resume_text}", resume_text)


def _strip_json_block(content: str) -> str:
    """Remove markdown code fence if present."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```\s*$", "", content)
    return content.strip()


def call_openai(prompt: str) -> str:
    """Call OpenAI API; return response content."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "OPENAI_API_KEY is not set. Set it in your environment or .env file."
        )

    client = OpenAI(api_key=api_key)
    log("Calling OpenAI API...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            timeout=API_TIMEOUT_SEC,
        )
    except Exception as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            raise TimeoutError(f"OpenAI API timed out after {API_TIMEOUT_SEC}s") from e
        raise
    msg = response.choices[0].message
    if not msg or not msg.content:
        raise ValueError("Empty response from OpenAI")
    return msg.content


def parse_profile(content: str) -> CandidateProfile:
    """Parse API response into CandidateProfile."""
    content = _strip_json_block(content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        log(f"JSON parse error: {e}")
        raise

    try:
        return CandidateProfile(**data)
    except Exception as e:
        log(f"CandidateProfile validation error: {e}")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract candidate profile from resume (PDF or TXT).")
    parser.add_argument("resume_path", type=Path, help="Path to resume .pdf or .txt file")
    args = parser.parse_args()

    try:
        log(f"Loading resume: {args.resume_path}")
        resume_text = load_resume_text(args.resume_path)
        log(f"Resume text length: {len(resume_text)} chars")

        prompt = load_prompt(resume_text)
        content = call_openai(prompt)
        profile = parse_profile(content)

        ensure_dirs()
        OUTPUT_PATH.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
        log(f"Saved to {OUTPUT_PATH}")

        compact = profile.compact_profile()
        print(f"Compact profile length: {len(compact)} characters")
        print("Done.")

    except FileNotFoundError as e:
        log(f"Error: {e}")
        sys.exit(1)
    except ValueError as e:
        log(f"Error: {e}")
        sys.exit(1)
    except TimeoutError as e:
        log(f"Timeout: {e}")
        sys.exit(1)
    except (json.JSONDecodeError, Exception) as e:
        log(f"Parse or API error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
