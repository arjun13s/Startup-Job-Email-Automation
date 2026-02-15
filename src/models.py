"""Pydantic models for pipeline rows."""

from pydantic import BaseModel, Field


def merge_dicts(left: dict, right: dict) -> dict:
    """Merge right into left; right overwrites left keys when non-empty."""
    result = dict(left)
    for key, value in right.items():
        if value != "" and value != []:
            result[key] = value
    return result


class CompanyRaw(BaseModel):
    """Raw company data from YC listing."""

    company_name: str = ""
    yc_batch: str = ""
    location: str = ""
    short_description: str = ""
    yc_company_url: str = ""
    website_url: str = ""

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class CompanyEnriched(CompanyRaw):
    """Company data enriched with jobs, contacts, and sponsorship info."""

    jobs_url: str = ""
    jobs_found: bool = False
    contact_emails: list[str] = Field(default_factory=list)
    founder_linkedins: list[str] = Field(default_factory=list)
    sponsorship_keywords_found: list[str] = Field(default_factory=list)
    sponsorship_evidence_url: str = ""
    homepage_text_snippet: str = ""
    careers_text_snippet: str = ""

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class CompanyScore(BaseModel):
    """Scoring and tiering for a company."""

    is_ca_or_ny: bool = False
    location_reason: str = ""
    wrapper_risk: str = "unknown"  # low/medium/high/unknown
    wrapper_reason: str = ""
    sponsorship_evidence_level: str = "unknown"  # none/weak/strong/unknown
    sponsorship_evidence: str = ""
    solid_score: int = 0  # 0..100
    solid_reasons: list[str] = Field(default_factory=list)
    tier: str = "standard"  # standard/ultra/skip

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class CompanyDraft(BaseModel):
    """Draft email content for a company."""

    tier: str = ""
    subject: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class CandidateProfile(BaseModel):
    """Resume/candidate profile data."""

    name: str = ""
    headline: str = ""
    core_skills: list[str] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list)
    quant_projects: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    education: str = ""
    links: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    def compact_profile(self) -> str:
        """Return a short formatted string (max 1200 chars) combining key profile fields."""
        parts: list[str] = []
        if self.headline:
            parts.append(f"Headline: {self.headline}")
        if self.core_skills:
            parts.append(f"Core skills: {', '.join(self.core_skills)}")
        if self.technical_skills:
            parts.append(f"Technical skills: {', '.join(self.technical_skills)}")
        if self.experience_highlights:
            top3 = self.experience_highlights[:3]
            parts.append("Top experience: " + " | ".join(top3))
        if self.quant_projects:
            top3 = self.quant_projects[:3]
            parts.append("Top projects: " + " | ".join(top3))
        if self.metrics:
            top3 = self.metrics[:3]
            parts.append("Metrics: " + " | ".join(top3))
        result = "\n".join(parts)
        return result[:1200] + ("..." if len(result) > 1200 else "")
