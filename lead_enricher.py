"""
Lead Enricher for ClientScout.

Enriches leads with additional data:
  1. Email finding — via Hunter.io API or pattern guessing
  2. Tech stack detection — via Wappalyzer-inspired heuristics
  3. Company info enrichment — size, industry verification

This is a NEW layer that has no equivalent in the Auto-Apply Twin
(where jobs come with complete data from the source).
"""

from __future__ import annotations

import json
import os
import re
import httpx
from urllib.parse import urlparse

# ── API Keys ───────────────────────────────────────────────────────────

HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")


# ══════════════════════════════════════════════════════════════════════════
#  EMAIL FINDING
# ══════════════════════════════════════════════════════════════════════════

# Common corporate email patterns
EMAIL_PATTERNS = [
    "{first}.{last}@{domain}",
    "{first}{last}@{domain}",
    "{first}.{last_init}@{domain}",
    "{first_init}.{last}@{domain}",
    "{first}@{domain}",
    "{last}@{domain}",
    "{first}_{last}@{domain}",
    "{first}{last_init}@{domain}",
    "{first_init}{last}@{domain}",
]


def find_email_via_hunter(domain: str, first_name: str = "", last_name: str = "") -> dict | None:
    """
    Find email using Hunter.io API.
    Free tier: 25 searches/month, 50 verifications/month.
    """
    if not HUNTER_API_KEY:
        return None

    try:
        params = {"domain": domain}
        if first_name and last_name:
            params["first_name"] = first_name
            params["last_name"] = last_name

        r = httpx.get(
            "https://api.hunter.io/v2/email-finder",
            params=params,
            headers={"Authorization": f"Bearer {HUNTER_API_KEY}"},
            timeout=15,
        )

        if r.status_code == 200:
            data = r.json().get("data", {})
            if data:
                return {
                    "email": data.get("email", ""),
                    "confidence": data.get("score", 0),
                    "verified": data.get("verification", {}).get("status") == "valid",
                    "source": "hunter",
                }
        elif r.status_code == 429:
            print("     ⚠ Hunter.io rate limit reached")

    except Exception as e:
        print(f"     ⚠ Hunter.io error: {e}")

    return None


def guess_email_pattern(company_name: str, domain: str) -> str | None:
    """Try to find the email pattern for a company by checking common patterns."""
    # Without actual email verification, we can't know the real pattern
    # This is a placeholder for when we have email verification API access
    return None


def generate_possible_emails(first_name: str, last_name: str, domain: str) -> list[dict]:
    """Generate possible email addresses using common corporate patterns."""
    if not first_name or not last_name or not domain:
        return []

    first = first_name.lower().strip()
    last = last_name.lower().strip()
    first_init = first[0] if first else ""
    last_init = last[0] if last else ""

    emails = []
    for pattern in EMAIL_PATTERNS:
        email = pattern.format(
            first=first,
            last=last,
            first_init=first_init,
            last_init=last_init,
            domain=domain,
        )
        emails.append({
            "email": email,
            "pattern": pattern,
            "confidence": "low",  # Not verified
            "source": "guess",
        })

    return emails


def extract_domain_from_website(website: str) -> str:
    """Extract clean domain from website URL."""
    if not website:
        return ""
    website = website.strip()
    if not website.startswith("http"):
        website = "https://" + website
    try:
        parsed = urlparse(website)
        domain = parsed.netloc or parsed.path
        domain = domain.replace("www.", "")
        return domain.split("/")[0]
    except Exception:
        return website.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]


def extract_name_parts(contact_name: str) -> tuple[str, str]:
    """Extract first and last name from a full name."""
    if not contact_name:
        return "", ""
    parts = contact_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


# ══════════════════════════════════════════════════════════════════════════
#  TECH STACK DETECTION
# ══════════════════════════════════════════════════════════════════════════

# Known tech signals from job descriptions and company names
TECH_SIGNALS = {
    ".net": [".net", "dotnet", "asp.net", "c#", "csharp", "blazor", "entity framework"],
    "python": ["python", "django", "flask", "fastapi", "pandas", "celery"],
    "azure": ["azure", "azure devops", "azure functions", "azure cloud"],
    "aws": ["aws", "amazon web services", "lambda", "ec2", "s3"],
    "react": ["react", "reactjs", "react.js", "next.js", "nextjs"],
    "angular": ["angular", "angularjs", "angular.js"],
    "vue": ["vue", "vuejs", "vue.js", "nuxt"],
    "node.js": ["node", "nodejs", "node.js", "express", "nestjs"],
    "postgresql": ["postgresql", "postgres", "psql"],
    "mysql": ["mysql", "mariadb"],
    "mongodb": ["mongodb", "mongo"],
    "docker": ["docker", "docker-compose", "dockerfile"],
    "kubernetes": ["kubernetes", "k8s", "k3s"],
    "java": ["java", "spring boot", "spring", "jvm", "kotlin"],
    "php": ["php", "laravel", "symfony", "wordpress"],
    "ruby": ["ruby", "rails", "ruby on rails"],
    "golang": ["golang", "go", "go programming"],
    "typescript": ["typescript", "ts", "type-safe"],
    "javascript": ["javascript", "js", "ecmascript"],
    "graphql": ["graphql", "apollo"],
    "redis": ["redis", "rediscache"],
    "elasticsearch": ["elasticsearch", "elastic", "elk stack"],
    "terraform": ["terraform", "infrastructure as code", "iac"],
    "ci/cd": ["ci/cd", "jenkins", "github actions", "gitlab ci", "circleci"],
    "microservices": ["microservices", "micro-services", "service mesh"],
}


def detect_tech_stack(text: str) -> list[str]:
    """Detect technologies mentioned in job descriptions or company text."""
    if not text:
        return []

    text_lower = text.lower()
    detected = []

    for tech, keywords in TECH_SIGNALS.items():
        for kw in keywords:
            if kw in text_lower:
                detected.append(tech)
                break  # One match per tech is enough

    return sorted(set(detected))


def detect_tech_stack_from_signals(lead: dict) -> list[str]:
    """Detect tech stack from all available lead signal data."""
    signal_data = lead.get("signal_data", {})
    if isinstance(signal_data, str):
        try:
            signal_data = json.loads(signal_data)
        except json.JSONDecodeError:
            signal_data = {}
    if not isinstance(signal_data, dict):
        signal_data = {}

    text_parts = []

    # Job titles
    job_titles = signal_data.get("job_titles", [])
    if isinstance(job_titles, list):
        text_parts.extend(str(t) for t in job_titles)
    elif isinstance(job_titles, str):
        text_parts.append(job_titles)

    # Job descriptions
    job_descs = signal_data.get("job_descriptions", [])
    if isinstance(job_descs, list):
        text_parts.extend(str(d) for d in job_descs)
    elif isinstance(job_descs, str):
        text_parts.append(job_descs)

    # Project description
    proj_desc = signal_data.get("project_description", "")
    if proj_desc:
        text_parts.append(str(proj_desc))

    # Notes
    notes = lead.get("notes", "")
    if notes:
        text_parts.append(notes)

    full_text = " ".join(text_parts)
    return detect_tech_stack(full_text)


# ══════════════════════════════════════════════════════════════════════════
#  MAIN ENRICHER
# ══════════════════════════════════════════════════════════════════════════

class LeadEnricher:
    """Enriches leads with additional data."""

    def __init__(self):
        self.hunter_available = bool(HUNTER_API_KEY)

    def enrich_contact(self, lead: dict) -> dict:
        """Find and verify contact email for a lead."""
        contact_name = lead.get("contact_name", "")
        contact_email = lead.get("contact_email", "")
        website = lead.get("website", "")

        first_name, last_name = extract_name_parts(contact_name)
        domain = extract_domain_from_website(website)

        result = {
            "email": contact_email,
            "email_verified": bool(lead.get("email_verified")),
            "email_source": "existing" if contact_email else "none",
            "possible_emails": [],
        }

        # If email already exists and is verified, skip
        if contact_email and lead.get("email_verified"):
            return result

        # Try Hunter.io first
        if self.hunter_available and domain and first_name:
            hunter_result = find_email_via_hunter(domain, first_name, last_name)
            if hunter_result:
                result["email"] = hunter_result["email"]
                result["email_verified"] = hunter_result["verified"]
                result["email_source"] = "hunter"
                result["email_confidence"] = hunter_result.get("confidence", 0)
                return result

        # Try common patterns
        if first_name and last_name and domain:
            possible = generate_possible_emails(first_name, last_name, domain)
            result["possible_emails"] = possible

        return result

    def enrich_tech_stack(self, lead: dict) -> dict:
        """Detect technology stack from lead signal data."""
        tech_stack = lead.get("tech_stack", [])
        if isinstance(tech_stack, str):
            try:
                tech_stack = json.loads(tech_stack)
            except json.JSONDecodeError:
                tech_stack = []

        # If already has tech stack data, return it
        if tech_stack and isinstance(tech_stack, list) and len(tech_stack) > 0:
            return {"tech_stack": tech_stack, "tech_source": "existing"}

        # Detect from signals
        detected = detect_tech_stack_from_signals(lead)
        return {
            "tech_stack": detected,
            "tech_source": "detected_from_signals" if detected else "none",
        }

    def enrich_lead(self, lead: dict) -> dict:
        """Run all enrichment on a lead. Returns updated lead dict with enriched fields."""
        result = dict(lead)

        # Contact enrichment
        contact_info = self.enrich_contact(lead)
        if contact_info.get("email") and not lead.get("contact_email"):
            result["contact_email"] = contact_info["email"]
        if contact_info.get("email_verified"):
            result["email_verified"] = 1

        # Tech stack enrichment
        tech_info = self.enrich_tech_stack(lead)
        new_tech = tech_info.get("tech_stack", [])
        if new_tech:
            existing = lead.get("tech_stack", [])
            if isinstance(existing, str):
                try:
                    existing = json.loads(existing)
                except json.JSONDecodeError:
                    existing = []
            if not existing or not isinstance(existing, list) or len(existing) == 0:
                result["tech_stack"] = json.dumps(new_tech)

        return result

    def enrich_leads(self, leads: list[dict]) -> list[dict]:
        """Enrich a batch of leads."""
        enriched = []
        for lead in leads:
            enriched.append(self.enrich_lead(lead))
        return enriched


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from leads_db import get_leads, update_lead_contact, get_stats, upsert_leads
    from lead_scoring import score_all

    # Enrich top 20 leads without contacts
    leads, _ = get_leads(status="discovered", min_score=30, limit=20)
    if not leads:
        leads, _ = get_leads(limit=20)

    print(f"Enriching {len(leads)} leads...")
    enricher = LeadEnricher()
    print(f"  Hunter.io: {'available' if enricher.hunter_available else 'not configured'}")

    updated = 0
    for lead in leads:
        enriched = enricher.enrich_lead(lead)

        # Update DB if new data found
        new_email = enriched.get("contact_email", "")
        if new_email and new_email != (lead.get("contact_email") or ""):
            update_lead_contact(
                lead["id"],
                contact_email=new_email,
                email_verified=enriched.get("email_verified", False),
            )
            updated += 1

        # Update tech stack
        tech_stack = enriched.get("tech_stack", "")
        if tech_stack and tech_stack != (lead.get("tech_stack") or "[]"):
            import sqlite3
            conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "leads.db"))
            conn.execute("UPDATE leads SET tech_stack = ?, updated_at = datetime('now') WHERE id = ?",
                         (tech_stack, lead["id"]))
            conn.commit()
            conn.close()
            updated += 1

    # Re-score enriched leads
    if updated > 0:
        from leads_db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM leads WHERE updated_at >= datetime('now', '-1 minutes')"
        ).fetchall()
        conn.close()
        if rows:
            rescored = score_all([dict(r) for r in rows])
            upsert_leads(rescored)
            print(f"  Re-scored {len(rescored)} leads after enrichment")

    print(f"Updated {updated} fields across leads")
    print(f"Hunter.io available: {enricher.hunter_available}")
    if not enricher.hunter_available:
        print("  Set HUNTER_API_KEY env var for email finding (https://hunter.io/api_keys)")
