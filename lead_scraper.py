#!/usr/bin/env python3
"""
ClientScout Lead Discovery — Buscador de señales de compra B2B.

A diferencia del job_scraper que busca ofertas de trabajo para postular,
este módulo busca SEÑALES de que una empresa necesita servicios de
automatización de procesos o desarrollo de software.

Fuentes:
  1. LinkedIn Jobs como señal — empresas contratando CTO, VP Eng, roles tech senior
  2. Upwork RSS — proyectos publicados en categorías relevantes

Uso:
  python3 lead_scraper.py discover
  python3 lead_scraper.py discover --source linkedin --limit 20
  python3 lead_scraper.py discover --source upwork --limit 30
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import argparse
import re
import hashlib
from datetime import datetime
from pathlib import Path
from collections import Counter
import urllib.parse

import httpx
from bs4 import BeautifulSoup

from scout_profile import PROFILE

OUTPUT_DIR = "./output_leads"

# ══════════════════════════════════════════════════════════════════════════
#  SIGNAL SEARCH CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

# Keywords that indicate BUYING INTENT (not job-seeking intent)
# These are companies we want to sell TO, not apply to
SIGNAL_SEARCHES = [
    # Urgent: companies hiring tech leadership
    {
        "keywords": "CTO OR 'VP Engineering' OR 'Head of Engineering' OR 'founding engineer'",
        "category": "hiring_cto",
        "signal_strength": "strong",
        "priority": 1,
    },
    # Strong: digital transformation = automation opportunity
    {
        "keywords": "'digital transformation' OR 'process automation' OR 'RPA developer' OR 'automation engineer'",
        "category": "transformation",
        "signal_strength": "strong",
        "priority": 2,
    },
    # Strong: legacy modernization = dev opportunity
    {
        "keywords": "'legacy system' OR modernization OR 'migrating from' OR 'system integration'",
        "category": "legacy_modernization",
        "signal_strength": "medium",
        "priority": 3,
    },
    # Moderate: general dev hiring spree
    {
        "keywords": "'senior .NET developer' OR 'lead developer' OR 'technical lead' OR 'senior software engineer'",
        "category": "hiring_spree",
        "signal_strength": "medium",
        "priority": 4,
    },
    # Niche: workflow/business automation
    {
        "keywords": "'workflow automation' OR 'business process' OR 'web scraping' OR 'API integration'",
        "category": "automation_need",
        "signal_strength": "medium",
        "priority": 5,
    },
]

# Locations to search (markets where Patricio operates)
SEARCH_LOCATIONS = [
    {"name": "Chile", "query": "Chile"},
    {"name": "United States", "query": "United States"},
    {"name": "Remote", "query": ""},  # Worldwide
    {"name": "Latin America", "query": "Latin America"},
    {"name": "Spain", "query": "Spain"},
]


# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 1: LINKEDIN JOBS AS BUY SIGNALS
# ══════════════════════════════════════════════════════════════════════════

def search_linkedin_signals(keyword: str, location: str = "",
                            limit: int = 25) -> list[dict]:
    """
    Search LinkedIn job postings and interpret them as BUY SIGNALS.
    A company posting a 'CTO' role doesn't just need a CTO —
    they need technology leadership, which means they may buy dev services too.

    Uses LinkedIn's public guest API (no auth required).
    """
    results = []
    encoded_kw = urllib.parse.quote(keyword.replace(" ", "+"))
    encoded_loc = urllib.parse.quote(location) if location else ""

    # LinkedIn guest API endpoint
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={encoded_kw}"
    if encoded_loc:
        url += f"&location={encoded_loc}"
    # Always search remote-friendly
    url += "&f_WT=2"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    }

    try:
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            print(f"     ⚠ LinkedIn returned {r.status_code}")
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("li")

        if not cards or len(cards) < 2:
            # Try alternative selectors
            cards = soup.select("div[class*='job-search-card'], .base-card, li[class*='card']")

        for card in cards[:limit]:
            # Extract job title
            title_el = (card.select_one("a[data-tracking-control*='title']") or
                       card.select_one("h3") or
                       card.select_one("a[class*='title']") or
                       card.select_one("[class*='title']"))
            title = title_el.get_text(strip=True) if title_el else ""

            # Extract company name
            company_el = (card.select_one("a[data-tracking-control*='company']") or
                         card.select_one("h4") or
                         card.select_one("[class*='company']") or
                         card.select_one("[class*='subtitle']"))
            company = company_el.get_text(strip=True) if company_el else ""

            # Extract location
            location_el = card.select_one("[class*='location'], [class*='bullet']")
            location_text = location_el.get_text(strip=True) if location_el else ""

            # Extract job link
            link_el = card.select_one("a[href*='/jobs/']")
            link = ""
            if link_el:
                link = link_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.linkedin.com" + link

            if not title or not company:
                continue

            # Build lead from this signal
            results.append({
                "company_name": company,
                "job_title": title,
                "location": location_text,
                "job_url": link,
                "linkedin_url": _infer_company_linkedin(company),
            })

    except httpx.TimeoutException:
        print(f"     ⚠ LinkedIn timeout for '{keyword}'")
    except Exception as e:
        print(f"     ⚠ LinkedIn error: {e}")

    return results


def _infer_company_linkedin(company_name: str) -> str:
    """Generate a likely LinkedIn company URL from company name."""
    slug = company_name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return f"https://www.linkedin.com/company/{slug}/"


def _extract_company_size(text: str) -> str:
    """Try to extract company size from description text."""
    patterns = [
        (r"(\d+)[\+]?\s*employees?", None),
        (r"(\d+)-(\d+)\s*employees?", None),
        (r"team of (\d+)", None),
    ]
    for pattern, _ in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            num = int(match.group(1))
            if num <= 10:
                return "1-10"
            elif num <= 50:
                return "11-50"
            elif num <= 200:
                return "51-200"
            elif num <= 500:
                return "201-500"
            else:
                return "501+"
    return ""


# ══════════════════════════════════════════════════════════════════════════
#  SOURCE 2: UPWORK RSS FEED
# ══════════════════════════════════════════════════════════════════════════

UPWORK_FEEDS = [
    {
        "url": "https://www.upwork.com/ab/feed/topics/rss?securityToken=39e5d9e25784675c48c2c7e2f1b6e6fe29adfe37856ff0657cece5ca7383b7397d0f5a854eab64442b900ec37e22e5fed19cb56a7b2e3e15baa2dde4acf1b69f&userUid=1096701366432579584&orgUid=1096701366443552769&topic=python",
        "category": "python_dev",
        "label": "Python Development",
    },
    {
        "url": "https://www.upwork.com/ab/feed/topics/rss?securityToken=39e5d9e25784675c48c2c7e2f1b6e6fe29adfe37856ff0657cece5ca7383b7397d0f5a854eab64442b900ec37e22e5fed19cb56a7b2e3e15baa2dde4acf1b69f&userUid=1096701366432579584&orgUid=1096701366443552769&topic=web-development",
        "category": "web_dev",
        "label": "Web Development",
    },
    {
        "url": "https://www.upwork.com/ab/feed/topics/rss?securityToken=39e5d9e25784675c48c2c7e2f1b6e6fe29adfe37856ff0657cece5ca7383b7397d0f5a854eab64442b900ec37e22e5fed19cb56a7b2e3e15baa2dde4acf1b69f&userUid=1096701366432579584&orgUid=1096701366443552769&topic=scraping",
        "category": "data_scraping",
        "label": "Data Scraping & Automation",
    },
]


def search_upwork_projects(feed_url: str = "", limit: int = 30) -> list[dict]:
    """
    Fetch projects from Upwork RSS feeds.
    Returns list of lead dicts with project details.
    """
    results = []
    feeds_to_check = [{"url": feed_url, "category": "custom"}] if feed_url else UPWORK_FEEDS

    for feed in feeds_to_check:
        try:
            r = httpx.get(feed["url"], headers={
                "User-Agent": "Mozilla/5.0 (compatible; ClientScout/1.0)",
                "Accept": "application/rss+xml, application/xml, text/xml",
            }, timeout=20, follow_redirects=True)

            if r.status_code != 200:
                print(f"     ⚠ Upwork feed returned {r.status_code}: {feed['label']}")
                continue

            soup = BeautifulSoup(r.text, "xml")
            if not soup.find("rss"):
                # Try as HTML
                soup = BeautifulSoup(r.text, "html.parser")

            items = soup.find_all("item")
            if not items:
                print(f"     ⚠ No items in feed: {feed['label']}")
                continue

            for item in items[:limit]:
                title = (item.find("title") or item.find("title")).get_text(strip=True) if (item.find("title") or item.find("title")) else ""
                description = item.find("description")
                desc_text = description.get_text(strip=True) if description else ""
                link = item.find("link")
                link_url = link.get_text(strip=True) if link else ""

                if not title:
                    continue

                # Extract budget if mentioned
                budget = _extract_budget(desc_text)
                # Extract company/client info from description
                company_name = _extract_company_from_description(desc_text)

                # Generate a synthetic LinkedIn URL for dedup
                company_slug = re.sub(r'[^a-z0-9]+', '-', (company_name or "upwork-client").lower()).strip('-')
                linkedin_url = f"upwork://{company_slug}"

                results.append({
                    "company_name": company_name or "Upwork Client",
                    "project_title": title,
                    "project_description": desc_text[:1000],
                    "project_url": link_url,
                    "estimated_budget": budget,
                    "linkedin_url": linkedin_url,
                    "category": feed["category"],
                })

            print(f"     ✓ Upwork ({feed['label']}): {len(items[:limit])} projects")

        except httpx.TimeoutException:
            print(f"     ⚠ Upwork timeout: {feed['label']}")
        except Exception as e:
            print(f"     ⚠ Upwork error ({feed['label']}): {e}")

    return results


def _extract_budget(text: str) -> float:
    """Try to extract a budget estimate from Upwork description."""
    patterns = [
        r"\$(\d{1,3},?\d{0,3})\s*(?:USD|dollars|budget|fixed)",
        r"budget.*?\$(\d{1,3},?\d{0,3})",
        r"(\d{1,3},?\d{0,3})\s*USD.*?budget",
        r"\$(\d{2,3})/hr",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                pass
    return 0


def _extract_company_from_description(text: str) -> str:
    """Try to extract company name from Upwork description."""
    # Common patterns in Upwork posts
    patterns = [
        r"(?:We are|I'm from|Company:\s*)([A-Z][a-zA-Z0-9\s&.]+?)(?:,|\.|\s+and|\s+we|\s+is|\s+looking)",
        r"([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){1,3})\s+(?:is looking|is seeking|needs)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if len(name) > 3 and not name.lower() in ("we are", "i am", "hello", "hi there"):
                return name
    return ""


# ══════════════════════════════════════════════════════════════════════════
#  LEAD NORMALIZATION (raw signals → unified lead dicts)
# ══════════════════════════════════════════════════════════════════════════

def normalize_linkedin_signal(raw: dict, search_config: dict, location: str) -> dict:
    """Convert a raw LinkedIn job card into a normalized lead dict."""
    company = raw.get("company_name", "")

    # Generate stable synthetic LinkedIn URL
    if raw.get("linkedin_url", "").startswith("https://www.linkedin.com/company/"):
        linkedin_url = raw["linkedin_url"]
    else:
        linkedin_url = _infer_company_linkedin(company)

    return {
        "company_name": company,
        "industry": _infer_industry(company, raw.get("job_title", "")),
        "company_size": _extract_company_size(raw.get("description", "")),
        "location": raw.get("location", ""),
        "website": "",
        "linkedin_url": linkedin_url,
        "signal_source": "linkedin_jobs",
        "signal_type": search_config["category"],
        "signal_strength": search_config["signal_strength"],
        "signal_data": json.dumps({
            "job_titles": [raw.get("job_title", "")],
            "job_urls": [raw.get("job_url", "")],
            "search_keywords": search_config["keywords"],
            "search_location": location,
            "num_roles": 1,
        }, ensure_ascii=False),
        "score_budget": 0,
        "score_need": 0,
        "score_accessibility": 0,
        "score_techfit": 0,
        "score_total": 0,
        "contact_name": "",
        "contact_title": "",
        "contact_linkedin": "",
        "contact_email": "",
        "tech_stack": json.dumps([]),
        "status": "discovered",
    }


def normalize_upwork_signal(raw: dict) -> dict:
    """Convert a raw Upwork project into a normalized lead dict."""
    return {
        "company_name": raw.get("company_name", "Upwork Client"),
        "industry": _infer_industry_from_text(raw.get("project_description", "")),
        "company_size": "unknown",
        "location": "Remote",
        "website": "",
        "linkedin_url": raw.get("linkedin_url", ""),
        "signal_source": "upwork",
        "signal_type": "project_post",
        "signal_strength": "medium",
        "signal_data": json.dumps({
            "project_title": raw.get("project_title", ""),
            "project_description": raw.get("project_description", "")[:2000],
            "project_url": raw.get("project_url", ""),
            "project_budget": raw.get("estimated_budget", 0),
            "upwork_category": raw.get("category", ""),
        }, ensure_ascii=False),
        "score_budget": 0,
        "score_need": 0,
        "score_accessibility": 0,
        "score_techfit": 0,
        "score_total": 0,
        "contact_name": "",
        "contact_title": "",
        "contact_linkedin": "",
        "contact_email": "",
        "tech_stack": json.dumps([]),
        "status": "discovered",
    }


def _infer_industry(company_name: str, job_title: str) -> str:
    """Try to infer industry from company name and job title."""
    text = f"{company_name} {job_title}".lower()
    industry_keywords = {
        "fintech": ["fintech", "financial", "banking", "payments", "crypto", "blockchain", "trading"],
        "healthtech": ["health", "medical", "clinic", "pharma", "biotech", "telemedicine"],
        "logistics": ["logistics", "shipping", "delivery", "fleet", "supply chain", "warehouse"],
        "ecommerce": ["ecommerce", "e-commerce", "retail", "shopify", "marketplace"],
        "saas": ["saas", "software", "platform", "cloud", "api"],
        "insurance": ["insurance", "insurtech", "claims"],
        "real estate": ["real estate", "property", "proptech", "rental"],
        "manufacturing": ["manufacturing", "factory", "industry 4.0", "production"],
        "legaltech": ["legal", "law", "compliance", "contract"],
        "edtech": ["education", "learning", "edtech", "training", "academy"],
        "engineering": ["engineering", "construction", "infrastructure"],
    }
    for industry, keywords in industry_keywords.items():
        if any(kw in text for kw in keywords):
            return industry
    return ""


def _infer_industry_from_text(text: str) -> str:
    """Infer industry from free text."""
    return _infer_industry(text, "")


# ══════════════════════════════════════════════════════════════════════════
#  MAIN DISCOVERY FUNCTION
# ══════════════════════════════════════════════════════════════════════════

def discover_leads(sources: list[str] | None = None,
                   limit_per_source: int = 25,
                   locations: list[str] | None = None) -> list[dict]:
    """
    Main discovery function. Searches all sources for buy signals.

    Args:
        sources: List of sources to search. None = all. Options: 'linkedin', 'upwork'
        limit_per_source: Max results per search
        locations: List of location names to search. None = all SEARCH_LOCATIONS

    Returns:
        List of normalized lead dicts ready for scoring and DB insertion.
    """
    if sources is None:
        sources = ["linkedin", "upwork"]
    if locations is None:
        locations = [loc["name"] for loc in SEARCH_LOCATIONS]

    all_leads = []
    seen_companies = set()  # Dedup by company name

    # ── LinkedIn Signals ──────────────────────────────────────────────
    if "linkedin" in sources:
        print("\n🔍 Searching LinkedIn for buy signals...")
        location_configs = [loc for loc in SEARCH_LOCATIONS if loc["name"] in locations]

        for search in SIGNAL_SEARCHES:
            for loc in location_configs:
                print(f"   Searching: '{search['keywords'][:60]}...' in {loc['name']}")

                raw_results = search_linkedin_signals(
                    keyword=search["keywords"],
                    location=loc["query"],
                    limit=limit_per_source,
                )

                for raw in raw_results:
                    lead = normalize_linkedin_signal(raw, search, loc["name"])

                    # Dedup by company name (case-insensitive)
                    company_key = lead["company_name"].lower().strip()
                    if company_key in seen_companies or not company_key:
                        continue
                    seen_companies.add(company_key)

                    all_leads.append(lead)

                time.sleep(1)  # Rate limiting between searches

        print(f"   ✓ LinkedIn signals: {len([l for l in all_leads if l['signal_source'] == 'linkedin_jobs'])} unique companies")

    # ── Upwork Projects ───────────────────────────────────────────────
    if "upwork" in sources:
        print("\n🔍 Searching Upwork for relevant projects...")

        raw_results = search_upwork_projects(limit=limit_per_source)

        for raw in raw_results:
            lead = normalize_upwork_signal(raw)

            company_key = lead["company_name"].lower().strip()
            if company_key in seen_companies or not company_key:
                continue
            seen_companies.add(company_key)

            all_leads.append(lead)

        print(f"   ✓ Upwork projects: {len([l for l in all_leads if l['signal_source'] == 'upwork'])} unique leads")

    print(f"\n📊 Total unique leads discovered: {len(all_leads)}")
    return all_leads


# ══════════════════════════════════════════════════════════════════════════
#  DEDUPLICATION (cross-source)
# ══════════════════════════════════════════════════════════════════════════

def deduplicate_leads(leads: list[dict]) -> list[dict]:
    """
    Merge leads that refer to the same company from different sources.
    Keeps the strongest signal and merges signal_data.
    """
    by_company = {}
    for lead in leads:
        key = lead["company_name"].lower().strip()
        if key not in by_company:
            by_company[key] = lead
        else:
            # Merge: keep highest signal_strength
            strength_order = {"strong": 3, "medium": 2, "weak": 1}
            existing = by_company[key]
            if strength_order.get(lead.get("signal_strength"), 0) > strength_order.get(existing.get("signal_strength"), 0):
                existing["signal_strength"] = lead["signal_strength"]
                existing["signal_type"] = lead["signal_type"]

            # Merge signal_data
            try:
                existing_data = json.loads(existing.get("signal_data", "{}"))
                new_data = json.loads(lead.get("signal_data", "{}"))
                if isinstance(existing_data, dict) and isinstance(new_data, dict):
                    for k, v in new_data.items():
                        if k in existing_data and isinstance(existing_data[k], list):
                            existing_data[k].extend(v)
                        else:
                            existing_data[k] = v
                    existing["signal_data"] = json.dumps(existing_data, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

    return list(by_company.values())


# ══════════════════════════════════════════════════════════════════════════
#  EXPORT
# ══════════════════════════════════════════════════════════════════════════

def export_leads_csv(leads: list[dict], filepath: str = ""):
    """Export leads to CSV for review."""
    if not filepath:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filepath = os.path.join(OUTPUT_DIR, f"leads_{timestamp}.csv")

    fieldnames = [
        "company_name", "industry", "company_size", "location",
        "signal_source", "signal_type", "signal_strength",
        "score_total", "status",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(leads)

    print(f"📁 Exported {len(leads)} leads to {filepath}")
    return filepath


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="ClientScout — B2B Lead Discovery Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 lead_scraper.py discover
  python3 lead_scraper.py discover --source linkedin --limit 20
  python3 lead_scraper.py discover --source upwork --limit 30
  python3 lead_scraper.py discover --source linkedin,upwork --location "United States"
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # discover command
    discover_parser = subparsers.add_parser("discover", help="Discover new leads from all sources")
    discover_parser.add_argument("--source", type=str, default="linkedin,upwork",
                                help="Sources to search (comma-separated: linkedin,upwork)")
    discover_parser.add_argument("--limit", type=int, default=25,
                                help="Max results per search (default: 25)")
    discover_parser.add_argument("--location", type=str, default="",
                                help="Specific location to search (default: all)")
    discover_parser.add_argument("--no-save", action="store_true",
                                help="Don't save to DB (print only)")
    discover_parser.add_argument("--csv", type=str, default="",
                                help="Export to CSV file path")
    discover_parser.add_argument("--campaign-id", type=int, default=0,
                                help="Scope discovery to a campaign's target countries/industries and auto-assign leads")

    args = parser.parse_args()

    if args.command == "discover":
        sources = [s.strip() for s in args.source.split(",")]

        # If campaign specified, scope locations and auto-assign
        campaign = None
        if args.campaign_id > 0:
            try:
                from leads_db import get_campaign
                campaign = get_campaign(args.campaign_id)
                if campaign:
                    print(f"\n📁 Campaign: {campaign['name']}")
                    target_countries = json.loads(campaign.get("target_countries", "[]"))
                    if isinstance(target_countries, list) and target_countries:
                        locations = target_countries
                        print(f"   Scoping to countries: {', '.join(target_countries)}")
                    else:
                        locations = [args.location] if args.location else None
                else:
                    locations = [args.location] if args.location else None
            except Exception as e:
                print(f"   ⚠ Could not load campaign: {e}")
                locations = [args.location] if args.location else None
        else:
            locations = [args.location] if args.location else None

        # Discover
        leads = discover_leads(
            sources=sources,
            limit_per_source=args.limit,
            locations=locations,
        )

        # Deduplicate
        leads = deduplicate_leads(leads)

        if not leads:
            print("\n⚠ No leads discovered. Try different keywords or sources.")
            return

        # Score leads
        try:
            from lead_scoring import score_all, classify_lead
            leads = score_all(leads)
            print("\n📊 Scoring complete:")
            for lead in leads[:10]:
                cls = classify_lead(lead["score_total"])
                print(f"   {lead['score_total']:.0f}/100 ({cls}) — {lead['company_name']} [{lead['signal_source']}]")
        except ImportError:
            print("⚠ lead_scoring module not available, skipping scoring")

        # Save to DB
        if not args.no_save:
            try:
                from leads_db import upsert_leads, get_stats, assign_lead_to_campaign
                count = upsert_leads(leads)
                print(f"\n💾 Saved {count} leads to database")

                # Auto-assign to campaign if specified
                if campaign and args.campaign_id > 0 and count > 0:
                    # Get the newly inserted leads (those with discovery date = today)
                    from leads_db import get_connection
                    conn = get_connection()
                    new_leads = conn.execute(
                        "SELECT id FROM leads WHERE date(discovered_at) = date('now') AND campaign_id IS NULL"
                    ).fetchall()
                    assigned = 0
                    for row in new_leads:
                        assign_lead_to_campaign(row["id"], args.campaign_id)
                        assigned += 1
                    conn.close()
                    if assigned > 0:
                        print(f"   📁 Auto-assigned {assigned} leads to campaign '{campaign['name']}'")

                stats = get_stats()
                ls = stats["leads"]
                print(f"   DB totals: {ls['total_leads']} leads | "
                      f"{ls['hot_leads']} hot | {ls['warm_leads']} warm | {ls['cold_leads']} cold")
            except ImportError:
                print("⚠ leads_db module not available, leads not saved to DB")

        # Export CSV
        if args.csv:
            export_leads_csv(leads, args.csv)
        else:
            export_leads_csv(leads)  # Auto-export to output dir

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
