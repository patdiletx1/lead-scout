#!/usr/bin/env python3
"""
Outreach Engine for ClientScout.

Orchestrates the multi-channel outreach pipeline:
  1. Select qualified leads
  2. Enrich with contact data
  3. Generate personalized messages via LLM
  4. Create draft outreach_attempts (approval queue)
  5. Execute approved sends (LinkedIn via Playwright, Email via SMTP)

MODE: Review & Approve — messages are generated as DRAFTS.
Nothing is sent without explicit approval from the dashboard.

Replaces apply_engine.py from the job search system.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime


class OutreachEngine:
    """Orchestrates the outreach pipeline for B2B leads."""

    def __init__(self, config: dict | None = None):
        self.config = config or {
            "min_score_to_outreach": 40,
            "max_drafts_per_run": 15,
            "delay_between_generations_s": 2,
        }

        self._writer = None
        self._enricher = None

    @property
    def writer(self):
        if self._writer is None:
            from llm_outreach_writer import LLMOutreachWriter
            self._writer = LLMOutreachWriter()
        return self._writer

    @property
    def enricher(self):
        if self._enricher is None:
            from lead_enricher import LeadEnricher
            self._enricher = LeadEnricher()
        return self._enricher

    def get_candidates(self, min_score: int = 0, limit: int = 20) -> list[dict]:
        """Get leads that are ready for outreach (no pending/sent attempts)."""
        from leads_db import get_leads_for_outreach
        if min_score == 0:
            min_score = self.config.get("min_score_to_outreach", 40)
        return get_leads_for_outreach(min_score=min_score, limit=limit)

    def preview(self, limit: int = 20) -> list[dict]:
        """Preview candidates without generating messages."""
        candidates = self.get_candidates(limit=limit)
        print(f"\n{'='*80}")
        print(f"OUTREACH PREVIEW — {len(candidates)} candidates (score >= {self.config.get('min_score_to_outreach', 40)})")
        print(f"{'='*80}")
        print(f"{'Score':<6} {'Company':<30} {'Industry':<14} {'Signal':<20} {'Contact':<20}")
        print("-" * 90)
        for c in candidates:
            contact = (c.get("contact_name") or "-")[:18]
            print(f"{c['score_total']:>5.0f}  {c['company_name'][:28]:<30} {(c.get('industry') or '-'):<14} "
                  f"{(c.get('signal_type') or '-'):<20} {contact:<20}")
        return candidates

    def generate_drafts(self, limit: int = 15) -> list[dict]:
        """
        Generate outreach drafts for qualified leads.

        1. Get candidates
        2. Enrich each lead
        3. Generate personalized message sequence
        4. Save as draft outreach_attempts

        Returns list of created outreach_attempt records.
        """
        from leads_db import (
            create_outreach_attempt, get_leads_for_outreach, log_activity,
            update_lead_contact,
        )
        from lead_scoring import score_all
        from leads_db import upsert_leads

        candidates = self.get_candidates(limit=limit)
        if not candidates:
            print("⚠ No candidates found for outreach")
            return []

        print(f"\n🚀 Generating outreach drafts for {len(candidates)} leads...")
        drafts_created = []

        for i, lead in enumerate(candidates):
            lead_id = lead["id"]
            company = lead.get("company_name", "Unknown")
            print(f"\n[{i+1}/{len(candidates)}] {company} (score: {lead['score_total']:.0f})")

            try:
                # Step 1: Enrich
                enriched = self.enricher.enrich_lead(lead)
                new_email = enriched.get("contact_email", "")
                if new_email and new_email != (lead.get("contact_email") or ""):
                    update_lead_contact(
                        lead_id,
                        contact_email=new_email,
                        email_verified=bool(enriched.get("email_verified")),
                    )
                    lead["contact_email"] = new_email
                    if enriched.get("email_verified"):
                        print(f"  ✓ Email found: {new_email}")

                # Update tech stack
                tech_stack = enriched.get("tech_stack", "")
                if isinstance(tech_stack, str):
                    try:
                        tech_stack = json.loads(tech_stack)
                    except json.JSONDecodeError:
                        tech_stack = []
                if tech_stack and not lead.get("tech_stack"):
                    import sqlite3
                    conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "leads.db"))
                    conn.execute("UPDATE leads SET tech_stack = ?, updated_at = datetime('now') WHERE id = ?",
                                 (json.dumps(tech_stack), lead_id))
                    conn.commit()
                    conn.close()
                    if tech_stack:
                        print(f"  ✓ Tech detected: {', '.join(tech_stack[:5])}")

                # Step 2: Re-score after enrichment
                from leads_db import get_lead
                updated_lead = get_lead(lead_id)
                if updated_lead:
                    from lead_scoring import score_lead
                    rescored = score_lead(updated_lead)
                    if rescored["score_total"] != updated_lead["score_total"]:
                        upsert_leads([rescored])
                        print(f"  ✓ Score updated: {updated_lead['score_total']:.0f} → {rescored['score_total']:.0f}")
                        lead = rescored

                # Step 3: Generate message sequence
                sequence = self.writer.generate_sequence(lead)
                print(f"  ✓ Generated {len(sequence)} messages")

                # Step 4: Save as drafts
                for msg in sequence:
                    attempt_id = create_outreach_attempt(
                        lead_id=lead_id,
                        channel=msg["channel"],
                        message_type=msg["message_type"],
                        subject=msg.get("subject", ""),
                        body=msg["body"],
                        generated_by=msg.get("generated_by", "llm_auto"),
                    )
                    drafts_created.append({
                        "id": attempt_id,
                        "lead_id": lead_id,
                        "company": company,
                        "channel": msg["channel"],
                        "message_type": msg["message_type"],
                    })

                # Rate limit between LLM calls
                if i < len(candidates) - 1:
                    time.sleep(self.config.get("delay_between_generations_s", 2))

            except Exception as e:
                print(f"  ⚠ Error: {e}")
                continue

        log_activity("outreach_drafts_generated",
                     f"Generated {len(drafts_created)} drafts for {len(candidates)} leads")
        print(f"\n✅ Created {len(drafts_created)} draft messages across {len(candidates)} leads")
        print(f"   Review them in the Approval Queue: https://leads.patdilet.dev")

        return drafts_created

    def run_enrichment(self, limit: int = 50) -> int:
        """
        Run enrichment on leads without contacts.
        Updates leads in-place and re-scores them.
        Returns number of leads updated.
        """
        from leads_db import get_leads, update_lead_contact, upsert_leads, log_activity
        from lead_scoring import score_all

        # Get leads that need enrichment (no contact, no tech stack)
        leads, _ = get_leads(status="discovered", limit=limit)
        if not leads:
            print("No leads to enrich")
            return 0

        print(f"\n🔍 Enriching {len(leads)} leads...")
        updated = 0

        for i, lead in enumerate(leads):
            if (i + 1) % 20 == 0:
                print(f"   ... {i+1}/{len(leads)}")

            enriched = self.enricher.enrich_lead(lead)
            changed = False

            # Update contact
            new_email = enriched.get("contact_email", "")
            if new_email and new_email != (lead.get("contact_email") or ""):
                update_lead_contact(
                    lead["id"],
                    contact_email=new_email,
                    email_verified=bool(enriched.get("email_verified")),
                )
                changed = True

            # Update tech stack
            tech = enriched.get("tech_stack", [])
            if isinstance(tech, str):
                try:
                    tech = json.loads(tech)
                except json.JSONDecodeError:
                    tech = []

            existing_tech = lead.get("tech_stack", [])
            if isinstance(existing_tech, str):
                try:
                    existing_tech = json.loads(existing_tech)
                except json.JSONDecodeError:
                    existing_tech = []

            if tech and not existing_tech:
                import sqlite3
                conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "leads.db"))
                conn.execute("UPDATE leads SET tech_stack = ?, updated_at = datetime('now') WHERE id = ?",
                             (json.dumps(tech), lead["id"]))
                conn.commit()
                conn.close()
                changed = True

            if changed:
                updated += 1

        # Re-score all updated leads
        if updated > 0:
            from leads_db import get_connection
            conn = get_connection()
            rows = conn.execute(
                "SELECT * FROM leads WHERE updated_at >= datetime('now', '-5 minutes')"
            ).fetchall()
            conn.close()
            if rows:
                rescored = score_all([dict(r) for r in rows])
                upsert_leads(rescored)
                print(f"   Re-scored {len(rescored)} leads after enrichment")

        log_activity("enrichment_run", f"Enriched {updated} leads")
        print(f"✅ Enrichment complete: {updated} leads updated")
        return updated

    def stats(self):
        """Show outreach pipeline stats."""
        from leads_db import get_stats
        stats = get_stats()
        ls = stats["leads"]
        os_ = stats["outreach"]

        print(f"\n{'='*60}")
        print("CLIENTSCOUT — Outreach Pipeline")
        print(f"{'='*60}")
        print(f"Total Leads:            {ls['total_leads']}")
        print(f"  Qualified (60+):      {ls['qualified']}")
        print(f"  Hot (80+):            {ls['hot_leads']}")
        print(f"  Warm (60-79):         {ls['warm_leads']}")
        print()
        print(f"Outreach Attempts:       {os_['total_attempts']}")
        print(f"  Pending Approval:      {os_['pending_approval']}")
        print(f"  Sent:                  {os_['sent']}")
        print(f"  Replied:               {os_['replied']}")
        print(f"  Response Rate:         {os_['response_rate']}%")
        print()
        print(f"Deals:                   {stats['deals']['total_deals']}")
        print(f"  Pipeline Value:        ${stats['deals']['total_value']:,.0f}")
        print(f"  Expected Value:        ${stats['deals']['total_expected_value']:,.0f}")

        # Per channel
        for ch in stats["outreach_by_channel"]:
            print(f"  {ch['channel']:<20} sent={ch.get('sent', 0)} replied={ch.get('replied', 0)}")


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ClientScout Outreach Engine")
    subparsers = parser.add_subparsers(dest="command")

    # preview
    subparsers.add_parser("preview", help="Preview outreach candidates")

    # generate
    gen_parser = subparsers.add_parser("generate", help="Generate outreach drafts")
    gen_parser.add_argument("--max", type=int, default=15, help="Max leads to generate for")

    # enrich
    enrich_parser = subparsers.add_parser("enrich", help="Enrich leads with contact/tech data")
    enrich_parser.add_argument("--limit", type=int, default=50, help="Max leads to enrich")

    # stats
    subparsers.add_parser("stats", help="Show outreach pipeline stats")

    # run (full cycle: enrich + generate)
    run_parser = subparsers.add_parser("run", help="Run full outreach cycle")
    run_parser.add_argument("--max", type=int, default=10, help="Max leads to process")

    args = parser.parse_args()
    engine = OutreachEngine()

    if args.command == "preview":
        engine.preview()

    elif args.command == "generate":
        engine.generate_drafts(limit=args.max)

    elif args.command == "enrich":
        engine.run_enrichment(limit=args.limit)

    elif args.command == "stats":
        engine.stats()

    elif args.command == "run":
        print("Step 1: Enrichment...")
        engine.run_enrichment(limit=50)
        print("\nStep 2: Generating drafts...")
        engine.generate_drafts(limit=args.max)
        print("\nStep 3: Stats...")
        engine.stats()

    else:
        parser.print_help()
