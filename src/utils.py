"""Utility functions for the pipeline."""

import csv
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Image extensions to exclude from email extraction
_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp"}
)

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


def log(msg: str) -> None:
    """Print message with timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def ensure_dirs() -> None:
    """Create data/raw, data/processed, data/final, configs/prompts, configs/templates if missing."""
    dirs = [
        _PROJECT_ROOT / "data" / "raw",
        _PROJECT_ROOT / "data" / "processed",
        _PROJECT_ROOT / "data" / "final",
        _PROJECT_ROOT / "configs" / "prompts",
        _PROJECT_ROOT / "configs" / "templates",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def read_csv(path: str | Path) -> list[dict]:
    """Read CSV file and return list of row dicts."""
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: str | Path, rows: list[dict]) -> None:
    """Write rows to CSV; headers inferred from first row."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.touch()
        return
    headers = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def normalize_url(base_url: str, href: str) -> str:
    """Resolve relative links safely against base_url."""
    if not href or not href.strip():
        return base_url
    href = href.strip()
    if href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return href
    return urljoin(base_url, href)


def extract_emails(text: str) -> list[str]:
    """Extract emails from text; de-duped, lowercased, excludes image extensions."""
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for match in _EMAIL_PATTERN.finditer(text):
        email = match.group(0).lower()
        if email in seen:
            continue
        # Exclude if domain part ends with image extension
        try:
            domain = email.split("@", 1)[1]
            ext = "." + domain.rsplit(".", 1)[-1].lower()
            if ext in _IMAGE_EXTENSIONS:
                continue
        except IndexError:
            pass
        seen.add(email)
        result.append(email)
    return result


def clean_text(s: str, max_len: int) -> str:
    """Collapse whitespace and truncate to max_len."""
    if not s:
        return ""
    cleaned = " ".join(s.split())
    return cleaned[:max_len] + ("..." if len(cleaned) > max_len else "")
