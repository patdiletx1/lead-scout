"""
Lead Scoring Engine for ClientScout.

Evaluates potential B2B clients based on:
- Budget Signal (30pts): Funding, company size, salary ranges in job posts
- Need Signal (35pts): Active hiring, tech debt, transformation mentions
- Accessibility (20pts): Decision-maker on LinkedIn, email findable
- Tech Fit (15pts): Industry match, tech stack overlap

Replaces scoring_engine.py from the job search system.
Follows the same pattern: score_one(lead) → score_all(leads).
"""

import json
import re
from scout_profile import PROFILE


def _kw_pattern(kw: str) -> re.Pattern:
    """Build a regex pattern for keyword matching.
    Handles dotted keywords (.NET) with literal escaping."""
    if "." in kw:
        escaped = re.escape(kw)
        return re.compile(escaped, re.IGNORECASE)
    return re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)


def _count_matches(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in text."""
    if not text:
        return 0
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        if _kw_pattern(kw).search(text_lower):
            count += 1
    return count


def _any_match(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in text."""
    if not text:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if _kw_pattern(kw).search(text_lower):
            return True
    return False


def _parse_signal_data(lead: dict) -> dict:
    """Robustly parse signal_data from a lead dict.
    Handles: JSON strings, double-encoded JSON, already-parsed dicts, empty/null."""
    signal_data = lead.get("signal_data", {})
    if isinstance(signal_data, dict):
        return signal_data
    if isinstance(signal_data, str):
        stripped = signal_data.strip()
        if not stripped or stripped == "null" or stripped == "{}":
            return {}
        # Try up to 2 levels of JSON decoding (handles double-encoding)
        for _ in range(2):
            try:
                parsed = json.loads(signal_data)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, str):
                    signal_data = parsed  # was double-encoded, try one more
                    continue
                return {}  # parsed to list/int/etc — return empty
            except (json.JSONDecodeError, TypeError, ValueError):
                return {}
    return {}


# ══════════════════════════════════════════════════════════════════════════
#  SCORING DIMENSIONS
# ══════════════════════════════════════════════════════════════════════════

def _score_budget_signal(lead: dict) -> tuple[float, str]:
    """Score budget signal (0-30).
    Returns (score, reason)."""
    signal_data = _parse_signal_data(lead)

    company_size = lead.get("company_size", "")
    signal_type = lead.get("signal_type", "")
    signal_strength = lead.get("signal_strength", "")

    # Funding signal (strongest budget indicator)
    has_funding = signal_data.get("funding_amount") or signal_data.get("funding_round")
    if has_funding:
        try:
            amount = float(signal_data.get("funding_amount", 0))
            if amount >= 10_000_000:  # $10M+
                return (30, f"Funding ${amount/1e6:.0f}M+")
            elif amount >= 5_000_000:
                return (28, f"Funding ${amount/1e6:.0f}M")
            elif amount >= 1_000_000:
                return (25, f"Funding ${amount/1e6:.0f}M")
            else:
                return (20, f"Funding ${amount/1e3:.0f}K")
        except (ValueError, TypeError):
            return (22, "Has funding (amount unknown)")

    # Company size as budget proxy
    if company_size in ("201-500", "501+"):
        return (25, f"Company size {company_size}")
    elif company_size == "51-200":
        return (20, f"Company size {company_size}")
    elif company_size == "11-50":
        # If they're hiring multiple roles, decent budget
        if signal_strength == "strong":
            return (18, "Small company, strong signal")
        return (10, f"Company size {company_size}")
    elif company_size == "1-10":
        if has_funding:
            return (15, "Small but funded")
        return (5, "Very small company")

    # Job post salary as budget proxy (from signal_data)
    job_salaries = signal_data.get("job_salaries", [])
    if job_salaries:
        avg_salary = sum(job_salaries) / len(job_salaries)
        if avg_salary >= 8000:
            return (22, f"High salary roles (avg ${avg_salary:.0f}/mo)")
        elif avg_salary >= 5000:
            return (18, f"Mid-high salary roles (avg ${avg_salary:.0f}/mo)")
        elif avg_salary >= 3000:
            return (12, f"Moderate salary roles (avg ${avg_salary:.0f}/mo)")

    return (5, "No clear budget signal")


def _score_need_signal(lead: dict) -> tuple[float, str]:
    """Score need signal (0-35).
    Returns (score, reason)."""
    signal_data = _parse_signal_data(lead)

    signal_type = lead.get("signal_type", "")
    signal_strength = lead.get("signal_strength", "")

    # Collect all text for keyword matching
    text_parts = []
    job_titles = signal_data.get("job_titles", [])
    job_descriptions = signal_data.get("job_descriptions", [])
    project_description = signal_data.get("project_description", "")
    notes = lead.get("notes", "")

    if isinstance(job_titles, list):
        text_parts.extend(job_titles)
    if isinstance(job_descriptions, list):
        text_parts.extend(job_descriptions)
    if project_description:
        text_parts.append(project_description)
    if notes:
        text_parts.append(notes)

    full_text = " ".join(text_parts)

    # Check urgent need signals
    urgent_kws = PROFILE["buy_signals"]["urgent_need"]
    strong_kws = PROFILE["buy_signals"]["strong_need"]
    moderate_kws = PROFILE["buy_signals"]["moderate_need"]

    urgent_matches = _count_matches(" ".join(job_titles if isinstance(job_titles, list) else []), urgent_kws)
    strong_matches = _count_matches(full_text, strong_kws)
    moderate_matches = _count_matches(full_text, moderate_kws)

    # Urgent: hiring CTO/VP Eng/Founding Engineer
    if urgent_matches > 0:
        return (35, f"Urgent: hiring leadership role ({urgent_matches} matches)")

    # Signal type from discovery
    if signal_type == "hiring_cto":
        return (33, "Actively hiring CTO/VP Engineering")

    # Multiple senior roles open
    if signal_strength == "strong" and signal_type == "hiring_spree":
        num_roles = signal_data.get("num_roles", 0) or signal_data.get("job_count", 0)
        if isinstance(num_roles, (int, float)) and num_roles >= 5:
            return (30, f"Hiring spree: {num_roles}+ tech roles open")

    if signal_type == "project_post":
        budget = signal_data.get("project_budget", 0)
        if isinstance(budget, (int, float)) and budget >= 5000:
            return (28, f"Active project with ${budget}+ budget")

    # Strong need keywords
    if strong_matches >= 3:
        return (28, f"Strong need: {strong_matches} signals detected")
    elif strong_matches >= 2:
        return (25, f"Need signals detected ({strong_matches})")

    if signal_type == "transformation":
        return (25, "Digital transformation initiative")

    if signal_type == "legacy_modernization":
        return (22, "Legacy system modernization")

    # Moderate need
    if moderate_matches >= 2:
        return (18, f"Moderate signals ({moderate_matches} matches)")
    elif moderate_matches >= 1:
        return (12, "Single moderate signal")

    # Funding sometimes implies need (they'll spend on tech)
    if signal_type == "funding":
        return (15, "Recently funded — likely to invest in tech")

    if signal_strength == "weak" and signal_type:
        return (8, f"Weak signal: {signal_type}")

    return (5, "No clear need signal")


def _score_accessibility(lead: dict) -> tuple[float, str]:
    """Score accessibility (0-20).
    Returns (score, reason)."""
    contact_name = lead.get("contact_name", "")
    contact_title = lead.get("contact_title", "")
    contact_linkedin = lead.get("contact_linkedin", "")
    contact_email = lead.get("contact_email", "")
    email_verified = lead.get("email_verified", False)

    target_roles = PROFILE["target_client"]["roles_to_contact"]

    has_name = bool(contact_name and contact_name.strip())
    has_linkedin = bool(contact_linkedin and contact_linkedin.strip())
    has_email = bool(contact_email and contact_email.strip())
    is_decision_maker = _any_match(contact_title, target_roles) if contact_title else False

    # Best case: decision-maker with LinkedIn AND verified email
    if is_decision_maker and has_linkedin and has_email and email_verified:
        return (20, f"Decision-maker ({contact_title}) with verified email + LinkedIn")

    if is_decision_maker and has_linkedin and has_email:
        return (18, f"Decision-maker ({contact_title}) with email + LinkedIn")

    if is_decision_maker and has_linkedin:
        return (15, f"Decision-maker ({contact_title}) on LinkedIn")

    if has_linkedin and has_email:
        return (12, "Contact info available (LinkedIn + email)")

    if has_linkedin:
        return (10, "LinkedIn contact available")

    if has_email:
        return (8, "Email available")

    if has_name:
        return (5, "Contact name known, no reachability")

    # Company has a website at least
    if lead.get("website"):
        return (3, "Company website available for contact form")

    return (0, "No contact point identified")


def _score_tech_fit(lead: dict) -> tuple[float, str]:
    """Score tech fit (0-15).
    Returns (score, reason)."""
    industry = lead.get("industry", "").lower()
    tech_stack = lead.get("tech_stack", [])
    if isinstance(tech_stack, str):
        try:
            tech_stack = json.loads(tech_stack)
        except (json.JSONDecodeError, TypeError):
            tech_stack = []

    signal_data = _parse_signal_data(lead)

    # Collect text from signal data for tech keyword matching
    text_parts = []
    job_descriptions = signal_data.get("job_descriptions", [])
    if isinstance(job_descriptions, list):
        text_parts.extend(job_descriptions)
    project_description = signal_data.get("project_description", "")
    if project_description:
        text_parts.append(project_description)
    full_text = " ".join(text_parts)

    score = 0
    reasons = []

    # Service-specific tech match
    services = PROFILE["services"]
    for svc in services:
        svc_match = _count_matches(full_text, svc["keywords"])
        if svc_match >= 3:
            score += 5
            reasons.append(f"Strong {svc['name']} fit")

    # Industry match
    target_industries = [i.lower() for i in PROFILE["target_client"]["industries"]]
    if industry in target_industries:
        score += 5
        reasons.append(f"Target industry: {industry}")
    elif industry and any(ti in industry for ti in target_industries):
        score += 3
        reasons.append(f"Adjacent industry: {industry}")

    # Tech stack alignment
    our_tech = {".net", "c#", "azure", "python", "sql server", "postgresql",
                "docker", "react", "next.js", "typescript", "node.js"}
    if isinstance(tech_stack, list) and tech_stack:
        matching = [t for t in tech_stack if t.lower() in our_tech]
        if matching:
            bonus = min(len(matching) * 2, 5)
            score += bonus
            reasons.append(f"Tech overlap: {', '.join(matching[:3])}")

    # Anti-fit: incompatible tech
    incompatible = {"ruby on rails", "php", "wordpress", "drupal", "golang", "rust", "swift", "kotlin"}
    if isinstance(tech_stack, list):
        incompatible_matches = [t for t in tech_stack if t.lower() in incompatible]
        if len(incompatible_matches) >= 3 and len(matching) == 0 if 'matching' in dir() else True:
            score = max(score - 5, 0)
            reasons.append(f"Incompatible stack: {', '.join(incompatible_matches[:3])}")

    # Exclude industries
    exclude = [e.lower() for e in PROFILE["exclude_industries"]]
    if industry in exclude:
        score = 0
        reasons = [f"Excluded industry: {industry}"]

    # Exclude keywords in text
    if _any_match(full_text, PROFILE["exclude_keywords"]):
        score = max(score - 15, 0)
        reasons.append("Exclusion keywords detected")

    if not reasons:
        reasons.append("Neutral tech fit")

    return (min(score, 15), "; ".join(reasons))


# ══════════════════════════════════════════════════════════════════════════
#  MAIN SCORING FUNCTION
# ══════════════════════════════════════════════════════════════════════════

def score_lead(lead: dict) -> dict:
    """Score a single lead. Returns the lead dict augmented with score fields."""
    budget, budget_reason = _score_budget_signal(lead)
    need, need_reason = _score_need_signal(lead)
    accessibility, acc_reason = _score_accessibility(lead)
    techfit, tech_reason = _score_tech_fit(lead)

    total = budget + need + accessibility + techfit

    result = dict(lead)
    result.update({
        "score_budget": round(budget, 1),
        "score_need": round(need, 1),
        "score_accessibility": round(accessibility, 1),
        "score_techfit": round(techfit, 1),
        "score_total": round(total, 1),
        "_score_reasons": {
            "budget": budget_reason,
            "need": need_reason,
            "accessibility": acc_reason,
            "techfit": tech_reason,
        },
    })

    # Auto-qualify if score >= 60
    if total >= 60 and lead.get("status") == "discovered":
        result["status"] = "qualified"

    return result


def score_all(leads: list[dict]) -> list[dict]:
    """Score a list of leads. Returns sorted by score descending."""
    scored = [score_lead(lead) for lead in leads]
    scored.sort(key=lambda l: l["score_total"], reverse=True)
    return scored


def classify_lead(score: float) -> str:
    """Classify a lead based on its score."""
    if score >= 80:
        return "hot"
    elif score >= 60:
        return "warm"
    elif score >= 40:
        return "cold"
    else:
        return "discard"


# ══════════════════════════════════════════════════════════════════════════
#  CLI (standalone testing)
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    # Test with sample leads
    sample_leads = [
        {
            "company_name": "TechStart Latam",
            "industry": "fintech",
            "company_size": "11-50",
            "signal_source": "linkedin_jobs",
            "signal_type": "hiring_cto",
            "signal_strength": "strong",
            "signal_data": {
                "job_titles": ["CTO", "Senior Backend Developer", "DevOps Engineer"],
                "job_descriptions": [
                    "Buscamos CTO para liderar transformación digital. Experiencia en automatización de procesos financieros.",
                    "Senior Backend con .NET y Azure para modernizar plataforma legacy.",
                ],
                "num_roles": 3,
            },
            "contact_name": "Maria Garcia",
            "contact_title": "CEO & Founder",
            "contact_linkedin": "https://linkedin.com/in/mariagarcia",
        },
        {
            "company_name": "LogisticsPro",
            "industry": "logistics",
            "company_size": "51-200",
            "signal_source": "upwork",
            "signal_type": "project_post",
            "signal_strength": "medium",
            "signal_data": {
                "project_description": "Need a developer to build a web scraping and automation system for tracking shipments across multiple carriers. Must integrate with our .NET backend.",
                "project_budget": 8000,
            },
        },
        {
            "company_name": "CasinoOnline Ltd",
            "industry": "gambling",
            "company_size": "201-500",
            "signal_source": "crunchbase",
            "signal_type": "funding",
            "signal_strength": "strong",
            "signal_data": {"funding_amount": 50000000},
        },
    ]

    scored = score_all(sample_leads)
    for lead in scored:
        classification = classify_lead(lead["score_total"])
        print(f"\n{'='*60}")
        print(f"Company: {lead['company_name']}")
        print(f"Score: {lead['score_total']:.0f}/100 ({classification})")
        print(f"  Budget: {lead['score_budget']:.0f}/30 — {lead['_score_reasons']['budget']}")
        print(f"  Need:   {lead['score_need']:.0f}/35 — {lead['_score_reasons']['need']}")
        print(f"  Access: {lead['score_accessibility']:.0f}/20 — {lead['_score_reasons']['accessibility']}")
        print(f"  Tech:   {lead['score_techfit']:.0f}/15 — {lead['_score_reasons']['techfit']}")
        print(f"  Status: {lead.get('status', 'discovered')}")
