"""
LLM Outreach Writer for ClientScout.

Generates personalized B2B outreach messages using DeepSeek (via Z.ai API).
Replaces the template-based cover_letter_gen.py from the job search system.

Message types:
  - linkedin_connect: Connection request (300 char max, no sales pitch)
  - linkedin_dm: Follow-up DM after connection accepted (value-first)
  - cold_email: Full cold email (subject + body, AIDA structure)
  - follow_up: Gentle follow-up after no response
  - breakup: Final breakup email
  - proposal_outline: Mini project proposal for hot leads

Uses the same LLM integration pattern as llm_application_writer.py:
  - DeepSeek via Z.ai API (OpenAI-compatible)
  - Retry with exponential backoff
  - Timeout 60s
"""

from __future__ import annotations

import json
import os
import time
import httpx
from scout_profile import PROFILE

# ── LLM Configuration ──────────────────────────────────────────────────

ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
MAX_RETRIES = 3
TIMEOUT = 60

# Fallback: DeepSeek direct API
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


def _get_api_config() -> tuple[str, str, str]:
    """Determine which API to use. Returns (api_key, base_url, model)."""
    if ZAI_API_KEY:
        return ZAI_API_KEY, ZAI_BASE_URL, LLM_MODEL
    if DEEPSEEK_API_KEY:
        return DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, "deepseek-chat"
    return "", "", LLM_MODEL


# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert B2B outreach writer for Patricio Diaz, a Senior Full Stack Developer & Process Automation Consultant with 10+ years of experience.

PATRICIO'S PROFILE:
- 10 years building .NET/C#, Python, and Azure solutions
- Specializes in: process automation (RPA, web scraping, workflow automation), custom software development, and AI/LLM integration
- Bilingual: Spanish (native) and English (B2+ professional)
- Based in Chile, works remotely with clients worldwide
- Portfolio site: https://patdilet.dev

SERVICES OFFERED:
1. Process Automation — End-to-end workflow automation, system integration, RPA, web scraping at scale ($5k-$25k projects)
2. Custom Software Development — Web apps, APIs, backend systems with .NET/C# and Python, legacy-to-cloud migrations ($10k-$50k projects)
3. AI/LLM Integration — Intelligent chatbots, document processing, autonomous agents, cognitive automation ($8k-$30k projects)

CASE STUDIES (reference naturally, don't force):
- OmniReport: SaaS that automated technical report generation, cutting 40+ hours/week to near-zero for engineering firms
- Auto-Apply Twin: Autonomous agent using Playwright + LLMs that automates job applications, 3x more applications/day
- AutoIVA: Tax automation agent for Chilean IRS, eliminating manual tax reconciliation errors
- DeliveryCheck AI: Computer vision PWA for delivery order verification, 98% error detection accuracy

WRITING RULES:
1. NEVER use clichés: no "I am excited to", no "I hope this email finds you well", no "I'm reaching out"
2. Lead with VALUE, not credentials. The prospect cares about their problem, not your bio.
3. Be direct and professional. B2B buyers respect conciseness.
4. Personalize: reference their company, industry, or specific signal detected.
5. Include exactly ONE soft call-to-action (not "let's jump on a call").
6. Keep LinkedIn connection requests under 280 characters (LinkedIn limit is 300).
7. Cold emails: use AIDA structure (Attention-Interest-Desire-Action) but keep under 150 words.
8. Match the language to the prospect's market: Spanish for LATAM/Spain leads, English for US/global.
9. NEVER make up details about their company you don't know for certain.
10. Sound like a consultant, not a job applicant. The tone is peer-to-peer, not subordinate.

OUTPUT FORMAT:
Return ONLY valid JSON with these exact keys:
{
  "subject": "email subject line (for email messages, empty string for LinkedIn)",
  "body": "the full message text",
  "tone": "professional|friendly|technical|direct",
  "case_study_used": "name of referenced case study or null"
}"""


# ── Message Type Prompts ───────────────────────────────────────────────

MESSAGE_PROMPTS = {
    "linkedin_connect": """Write a LinkedIn connection request for this prospect.

CONTEXT:
- Company: {company_name}
- Industry: {industry}
- Signal detected: {signal_type} ({signal_strength})
- Prospect name: {contact_name}
- Prospect title: {contact_title}
- Prospect language: {language}

INSTRUCTIONS:
- Maximum 280 characters (LinkedIn limit is 300)
- NO sales pitch — this is just a connection request
- Mention something specific about their company or situation
- If they're hiring a CTO/VP Eng, hint that you work with companies in transition
- If they're in digital transformation, mention automation experience naturally
- Sound like a peer, not a vendor
- Write in {language}""",

    "linkedin_dm": """Write a LinkedIn follow-up message after they accepted your connection request.

CONTEXT:
- Company: {company_name}
- Industry: {industry}
- Signal detected: {signal_type} ({signal_strength})
- Prospect name: {contact_name}
- Prospect title: {contact_title}
- Prospect language: {language}
- Their situation: {context_notes}

INSTRUCTIONS:
- Thank them for connecting (briefly, 3-5 words max)
- Reference the specific signal we detected about their company
- Mention one relevant case study naturally
- Offer a soft next step: "would you be open to a 15-minute chat about [specific topic]?"
- Keep under 500 characters
- Write in {language}""",

    "cold_email": """Write a cold outreach email for this prospect.

CONTEXT:
- Company: {company_name}
- Industry: {industry}
- Signal detected: {signal_type} ({signal_strength})
- Prospect name: {contact_name}
- Prospect title: {contact_title}
- Prospect language: {language}
- Their situation: {context_notes}

INSTRUCTIONS:
- Subject line: personalized, under 50 chars, no spam words, mention their company or situation
- Body: AIDA structure (get Attention, build Interest, create Desire, prompt Action)
- Reference the specific signal (e.g., if they're hiring a CTO, position yourself as interim tech leadership or project-based support)
- Include one relevant case study naturally
- CTA: soft ask — "worth a 15-min chat?" or "happy to share how we did this for [similar company]"
- Total body under 150 words
- Write in {language}""",

    "follow_up": """Write a gentle follow-up to a prospect who hasn't responded in 7 days.

CONTEXT:
- Company: {company_name}
- Previous message: {previous_message}
- Days since contact: 7
- Prospect language: {language}

INSTRUCTIONS:
- Don't be pushy or passive-aggressive
- Add a tiny bit of new value (a relevant insight, article, or thought)
- Keep it very short (under 100 words)
- Write in {language}""",

    "breakup": """Write a final breakup email. This is the last touch in a sequence.

CONTEXT:
- Company: {company_name}
- Prospect name: {contact_name}
- Prospect language: {language}

INSTRUCTIONS:
- Don't burn bridges — leave the door open
- Mention they can reach out anytime if things change
- Keep it very short (under 80 words)
- No guilt-tripping
- Write in {language}""",

    "proposal_outline": """Write a mini project proposal outline for a highly qualified lead.

CONTEXT:
- Company: {company_name}
- Industry: {industry}
- Signal detected: {signal_type}
- Prospect name: {contact_name}
- Prospect language: {language}
- Their situation: {context_notes}

INSTRUCTIONS:
- Structure: Problem → Proposed Solution → Approach → Timeline → Estimated Investment
- Reference relevant case study with concrete metrics
- Include a rough timeline (e.g., "4-6 weeks for MVP")
- Include a budget range based on project type
- Keep it concise — this is a conversation starter, not a contract
- Write in {language}

Output as JSON with keys: "problem_statement", "proposed_solution", "approach", "timeline", "estimated_investment", "case_study_reference".""",
}


# ── Core Writer ────────────────────────────────────────────────────────

class LLMOutreachWriter:
    """Generates personalized B2B outreach messages using LLM."""

    def __init__(self, api_key: str = "", model: str = ""):
        self.api_key = api_key or _get_api_config()[0]
        self.base_url = api_key and _get_api_config()[1] or ""
        self.model = model or _get_api_config()[2]

        if not self.api_key:
            print("⚠ LLMOutreachWriter: No API key configured. Messages will use templates.")
        if not self.base_url and self.api_key:
            self.base_url = _get_api_config()[1]

    def _call_llm(self, system_prompt: str, user_prompt: str) -> dict | None:
        """Call the LLM API with retry logic."""
        if not self.api_key:
            return None

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        for attempt in range(MAX_RETRIES):
            try:
                r = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 800,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=TIMEOUT,
                )

                if r.status_code == 200:
                    data = r.json()
                    content = data["choices"][0]["message"]["content"]
                    return json.loads(content)
                elif r.status_code == 429:
                    wait = min(2 ** attempt * 5, 30)
                    time.sleep(wait)
                else:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)

            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
            except json.JSONDecodeError:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1)

        return None

    def _detect_language(self, lead: dict) -> str:
        """Detect best language for outreach based on lead location/industry."""
        location = (lead.get("location") or "").lower()
        es_regions = ["chile", "argentina", "mexico", "colombia", "peru", "uruguay",
                      "spain", "españa", "latam", "latin america", "brazil", "brasil",
                      "madrid", "barcelona", "santiago", "buenos aires", "bogota",
                      "lima", "montevideo", "são paulo", "medellin", "granada",
                      "andalus", "providencia"]

        for region in es_regions:
            if region in location:
                return "Spanish (es)"

        return "English (en)"

    def _build_context_notes(self, lead: dict) -> str:
        """Build context notes from lead signal data for prompt personalization."""
        signal_data = lead.get("signal_data", {})
        if isinstance(signal_data, str):
            try:
                signal_data = json.loads(signal_data)
            except json.JSONDecodeError:
                signal_data = {}

        notes = []

        signal_type = lead.get("signal_type", "")
        if signal_type == "hiring_cto":
            notes.append("This company is hiring a CTO/VP of Engineering — they lack technical leadership. "
                         "They likely need interim tech leadership or project-based development support "
                         "while they search for a permanent hire.")
        elif signal_type == "hiring_spree":
            notes.append("This company is hiring multiple senior technical roles — they're scaling "
                         "their engineering team and may need overflow capacity or specialized skills.")
        elif signal_type == "transformation":
            notes.append("This company has a digital transformation initiative — they're investing "
                         "in modernizing processes, which is exactly what Patricio's automation services address.")
        elif signal_type == "legacy_modernization":
            notes.append("This company is migrating or modernizing legacy systems — they need "
                         "experienced developers who understand both old and new stacks.")
        elif signal_type == "automation_need":
            notes.append("This company has explicit automation needs — perfect fit for Patricio's "
                         "process automation services (RPA, web scraping, workflow automation).")
        elif signal_type == "project_post":
            budget = signal_data.get("project_budget", 0)
            desc = signal_data.get("project_description", "")
            notes.append(f"Active project posted. Budget: ${budget}. Description: {desc[:200]}")

        # Add industry context
        industry = lead.get("industry", "")
        if industry == "fintech":
            notes.append("Fintech industry — likely needs secure, compliant automation solutions.")
        elif industry == "logistics":
            notes.append("Logistics industry — likely needs shipment tracking, inventory automation, or delivery verification.")
        elif industry == "saas":
            notes.append("SaaS company — likely needs API integrations, workflow automation, or platform development.")
        elif industry == "healthtech":
            notes.append("Healthtech — likely needs HIPAA-compliant data processing or document automation.")

        return " ".join(notes) if notes else "No specific context available."

    def generate(self, lead: dict, message_type: str = "cold_email",
                 previous_message: str = "") -> dict:
        """
        Generate an outreach message for a lead.

        Args:
            lead: Lead dict from leads_db
            message_type: One of 'linkedin_connect', 'linkedin_dm', 'cold_email',
                         'follow_up', 'breakup', 'proposal_outline'
            previous_message: Previous message body (for follow_up)

        Returns:
            dict with keys: subject, body, tone, case_study_used, generated_by
        """
        language = self._detect_language(lead)
        context_notes = self._build_context_notes(lead)

        prompt_template = MESSAGE_PROMPTS.get(message_type)
        if not prompt_template:
            return self._fallback_message(lead, message_type, language)

        user_prompt = prompt_template.format(
            company_name=lead.get("company_name", "your company"),
            industry=lead.get("industry", "technology"),
            signal_type=lead.get("signal_type", "technology needs"),
            signal_strength=lead.get("signal_strength", "medium"),
            contact_name=lead.get("contact_name", "there"),
            contact_title=lead.get("contact_title", "hiring manager"),
            language=language,
            context_notes=context_notes,
            previous_message=previous_message or "(first contact)",
        )

        result = self._call_llm(SYSTEM_PROMPT, user_prompt)

        if result:
            return {
                "subject": result.get("subject", ""),
                "body": result.get("body", ""),
                "tone": result.get("tone", "professional"),
                "case_study_used": result.get("case_study_used"),
                "generated_by": "llm_auto",
            }

        # Fallback to template
        return self._fallback_message(lead, message_type, language)

    def generate_sequence(self, lead: dict) -> list[dict]:
        """
        Generate a full outreach sequence for a lead.

        Returns list of message dicts with channel, message_type, and content.
        Sequence:
          1. LinkedIn connection request
          2. LinkedIn DM (for after connection accepted)
          3. Cold email
          4. Follow-up email
        """
        sequence = []
        language = self._detect_language(lead)
        has_contact = bool(lead.get("contact_name", "").strip())

        # 1. LinkedIn connection request (always, uses company name if no contact)
        msg1 = self.generate(lead, "linkedin_connect")
        sequence.append({
            "channel": "linkedin_connect",
            "message_type": "connection_request",
            "subject": "",
            "body": msg1["body"],
            "tone": msg1.get("tone", "professional"),
            "case_study_used": msg1.get("case_study_used"),
            "generated_by": msg1.get("generated_by", "template"),
        })

        # 2. LinkedIn DM (for after connection accepted)
        if has_contact:
            msg2 = self.generate(lead, "linkedin_dm")
            sequence.append({
                "channel": "linkedin_dm",
                "message_type": "follow_up",
                "subject": "",
                "body": msg2["body"],
                "tone": msg2.get("tone", "professional"),
                "case_study_used": msg2.get("case_study_used"),
                "generated_by": msg2.get("generated_by", "template"),
            })

        # 3. Cold email (if email available)
        if lead.get("contact_email", "").strip():
            msg3 = self.generate(lead, "cold_email")
            sequence.append({
                "channel": "email",
                "message_type": "cold_email",
                "subject": msg3.get("subject", f"Automatizacion de procesos para {lead.get('company_name', 'su empresa')}" if "spanish" in language.lower() else f"Process automation for {lead.get('company_name', 'your team')}"),
                "body": msg3["body"],
                "tone": msg3.get("tone", "professional"),
                "case_study_used": msg3.get("case_study_used"),
                "generated_by": msg3.get("generated_by", "template"),
            })

            # 4. Follow-up email (generated as draft, sent 7 days later)
            msg4 = self.generate(lead, "follow_up", previous_message=msg3.get("body", ""))
            sequence.append({
                "channel": "email",
                "message_type": "follow_up",
                "subject": "Re: " + msg3.get("subject", ""),
                "body": msg4["body"],
                "tone": msg4.get("tone", "professional"),
                "case_study_used": msg4.get("case_study_used"),
                "generated_by": msg4.get("generated_by", "template"),
            })

        return sequence

    def _fallback_message(self, lead: dict, message_type: str, language: str) -> dict:
        """Generate a template-based message when LLM is unavailable."""
        company = lead.get("company_name", "your company")
        industry = lead.get("industry", "technology")
        signal_type = lead.get("signal_type", "")
        contact = lead.get("contact_name", "there")
        is_spanish = "spanish" in (language or "").lower()

        templates = {
            "linkedin_connect": {
                "es": f"Hola, vi que {company} esta en busqueda de liderazgo tech. Soy dev full-stack especializado en automatizacion de procesos. Conectemos para estar en contacto.",
                "en": f"Hi, noticed {company} is hiring tech leadership. I'm a full-stack dev specialized in process automation for {industry}. Would be great to connect.",
            },
            "linkedin_dm": {
                "es": f"Gracias por conectar. He trabajado con empresas {industry} automatizando procesos criticos — desde RPA hasta integraciones complejas. Si estan evaluando soluciones de automatizacion, feliz de compartir como lo hemos hecho con casos similares.",
                "en": f"Thanks for connecting. I've helped {industry} companies automate critical workflows — from RPA to complex system integrations. If you're exploring automation solutions, happy to share how we've done it for similar teams.",
            },
            "cold_email": {
                "es": f"Automatizacion de procesos para {company}",
                "en": f"Process automation — {company}",
            },
        }

        if message_type == "cold_email":
            subject = templates["cold_email"]["es"] if is_spanish else templates["cold_email"]["en"]
            body_en = f"""Hi {contact},

I noticed {company} is actively hiring for tech leadership roles. Companies at this stage often need development capacity before the permanent hire starts — or project-based support for specific initiatives.

I specialize in process automation and custom development for {industry} companies. Recently, we built an autonomous agent system for a fintech that eliminated 95% of manual report generation time.

Would a 15-minute chat be worth exploring if there's overlap with what you're building?

Best,
Patricio Diaz
https://patdilet.dev"""
            body_es = f"""Hola {contact},

Vi que {company} esta contratando roles de liderazgo tech. En mi experiencia, empresas en esta etapa suelen necesitar capacidad de desarrollo antes de que el hire permanente empiece — o apoyo en proyectos especificos.

Me especializo en automatizacion de procesos y desarrollo a medida para empresas {industry}. Hace poco construimos un agente autonomo para una fintech que elimino el 95% del tiempo de generacion de reportes.

¿Te sirve una charla de 15 minutos para ver si hay algo en lo que pueda ayudar?

Saludos,
Patricio Diaz
https://patdilet.dev"""
            return {
                "subject": subject,
                "body": body_es if is_spanish else body_en,
                "tone": "professional",
                "case_study_used": "OmniReport",
                "generated_by": "template",
            }

        t = templates.get(message_type, templates["linkedin_connect"])
        body = t["es"] if is_spanish else t["en"]
        return {
            "subject": "",
            "body": body[:290],  # Truncate for LinkedIn limits
            "tone": "professional",
            "case_study_used": None,
            "generated_by": "template",
        }


# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from leads_db import get_lead, get_leads_for_outreach

    # Test: generate messages for top lead
    writer = LLMOutreachWriter()

    candidates = get_leads_for_outreach(min_score=30, limit=3)
    if not candidates:
        # Get any lead for testing
        from leads_db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT * FROM leads ORDER BY score_total DESC LIMIT 1").fetchone()
        conn.close()
        if row:
            candidates = [dict(row)]

    for lead in candidates:
        print(f"\n{'='*70}")
        print(f"Company: {lead['company_name']} (Score: {lead['score_total']:.0f})")
        print(f"Signal: {lead.get('signal_type')} | Industry: {lead.get('industry', '—')}")
        print(f"Language: {writer._detect_language(lead)}")

        if writer.api_key:
            print(f"  [LLM mode: {writer.model}]")
            sequence = writer.generate_sequence(lead)
        else:
            print("  [Template mode — no API key]")
            sequence = writer.generate_sequence(lead)

        for i, msg in enumerate(sequence):
            print(f"\n--- Step {i+1}: {msg['channel']} ({msg['message_type']}) ---")
            if msg.get("subject"):
                print(f"Subject: {msg['subject']}")
            print(f"Body: {msg['body'][:200]}...")
            if msg.get("case_study_used"):
                print(f"Case study: {msg['case_study_used']}")
