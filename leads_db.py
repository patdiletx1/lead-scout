"""
SQLite database for ClientScout — B2B lead discovery, qualification, and outreach pipeline.
Tracks leads, outreach attempts, deals, and pipeline status.

Follows the same patterns as jobs_db.py (WAL mode, row_factory, ON CONFLICT dedup,
whitelist-based sort, auto-init on import).
"""

from __future__ import annotations

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "leads.db")


def get_connection():
    """Get connection with row_factory for dict access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        -- Main leads table
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            industry TEXT,
            company_size TEXT,           -- '1-10', '11-50', '51-200', '201-500', '501+'
            location TEXT,
            website TEXT,
            linkedin_url TEXT UNIQUE,    -- Deduplication key

            -- Señales detectadas
            signal_source TEXT,          -- linkedin_jobs, upwork, crunchbase, clutch, reddit, hn
            signal_type TEXT,            -- hiring_cto, funding, project_post, transformation, legacy_modernization
            signal_strength TEXT,        -- strong, medium, weak
            signal_data TEXT,            -- JSON: raw signal data (job posts, funding amount, project description, etc.)

            -- Scoring (0-100 total)
            score_budget REAL DEFAULT 0,       -- Max 30
            score_need REAL DEFAULT 0,         -- Max 35
            score_accessibility REAL DEFAULT 0, -- Max 20
            score_techfit REAL DEFAULT 0,       -- Max 15
            score_total REAL DEFAULT 0,

            -- Contact info
            contact_name TEXT,
            contact_title TEXT,
            contact_linkedin TEXT,
            contact_email TEXT,
            email_verified INTEGER DEFAULT 0,

            -- Tech stack (JSON array of detected technologies)
            tech_stack TEXT,

            -- Pipeline status
            status TEXT DEFAULT 'discovered',
            -- discovered → qualified → contacted → in_discussion → proposal_sent → won → lost
            notes TEXT,

            -- Timestamps
            discovered_at TEXT DEFAULT (datetime('now')),
            last_contacted_at TEXT,
            next_action_at TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Outreach attempts (messages sent or drafts pending approval)
        CREATE TABLE IF NOT EXISTS outreach_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id) ON DELETE CASCADE,
            channel TEXT,                -- linkedin_connect, linkedin_dm, email, upwork, contact_form
            direction TEXT DEFAULT 'outbound',  -- outbound, inbound
            message_type TEXT,           -- connection_request, follow_up, cold_email, proposal, breakup, reply
            subject TEXT,
            body TEXT,
            generated_by TEXT DEFAULT 'llm_auto',  -- llm_auto, llm_manual, manual
            status TEXT DEFAULT 'draft', -- draft, approved, sent, opened, replied, bounced, failed, rejected
            response_text TEXT,
            sent_at TEXT,
            responded_at TEXT,
            metadata TEXT,               -- JSON: email_message_id, linkedin_message_id, etc.
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Deals / Projects
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
            title TEXT,
            description TEXT,
            estimated_value_usd REAL,
            estimated_duration_months INTEGER,
            probability_pct REAL DEFAULT 10,  -- Probability of closing (0-100)
            expected_value REAL,              -- value * probability / 100
            stage TEXT DEFAULT 'discovery',   -- discovery, scoping, negotiation, closed_won, closed_lost
            closed_at TEXT,
            closed_reason TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Activity log
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            action TEXT NOT NULL,
            details TEXT
        );

        -- Configuration (same pattern as auto_apply_config in jobs_db)
        CREATE TABLE IF NOT EXISTS scout_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Campaigns for organizing outreach by target market
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            target_countries TEXT,     -- JSON array: ["Chile", "Argentina"]
            target_industries TEXT,    -- JSON array: ["fintech", "saas"]
            target_services TEXT,      -- JSON array: ["process_automation", "custom_development"]
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- Indexes
        CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score_total DESC);
        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
        CREATE INDEX IF NOT EXISTS idx_leads_discovered ON leads(discovered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_leads_industry ON leads(industry);
        CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_attempts(status);
        CREATE INDEX IF NOT EXISTS idx_outreach_lead ON outreach_attempts(lead_id);
        CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
        CREATE INDEX IF NOT EXISTS idx_deals_lead ON deals(lead_id);
    """)

    # Migrations: add campaign_id to leads if not exists (must be before index creation)
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id)")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Create index after column is guaranteed to exist
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_campaign ON leads(campaign_id)")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  LEAD CRUD
# ══════════════════════════════════════════════════════════════════════════

def upsert_leads(leads: list[dict]) -> int:
    """Insert or update leads. Deduplicates by linkedin_url.
    Returns count of new/updated leads."""
    conn = get_connection()
    count = 0
    for lead in leads:
        linkedin_url = lead.get("linkedin_url", "")
        if not linkedin_url:
            # Generate a synthetic dedup key from company_name + signal_source
            linkedin_url = f"synthetic://{lead.get('signal_source', 'unknown')}/{lead.get('company_name', 'unknown').lower().replace(' ', '-')}"

        try:
            conn.execute("""
                INSERT INTO leads (
                    company_name, industry, company_size, location, website,
                    linkedin_url, signal_source, signal_type, signal_strength,
                    signal_data, score_budget, score_need, score_accessibility,
                    score_techfit, score_total, contact_name, contact_title,
                    contact_linkedin, contact_email, email_verified, tech_stack,
                    status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(linkedin_url) DO UPDATE SET
                    signal_source = excluded.signal_source,
                    signal_type = excluded.signal_type,
                    signal_strength = excluded.signal_strength,
                    signal_data = excluded.signal_data,
                    score_budget = excluded.score_budget,
                    score_need = excluded.score_need,
                    score_accessibility = excluded.score_accessibility,
                    score_techfit = excluded.score_techfit,
                    score_total = excluded.score_total,
                    tech_stack = COALESCE(excluded.tech_stack, leads.tech_stack),
                    updated_at = datetime('now')
            """, (
                lead.get("company_name", ""),
                lead.get("industry", ""),
                lead.get("company_size", ""),
                lead.get("location", ""),
                lead.get("website", ""),
                linkedin_url,
                lead.get("signal_source", ""),
                lead.get("signal_type", ""),
                lead.get("signal_strength", ""),
                json.dumps(lead.get("signal_data", {}), ensure_ascii=False),
                lead.get("score_budget", 0),
                lead.get("score_need", 0),
                lead.get("score_accessibility", 0),
                lead.get("score_techfit", 0),
                lead.get("score_total", 0),
                lead.get("contact_name", ""),
                lead.get("contact_title", ""),
                lead.get("contact_linkedin", ""),
                lead.get("contact_email", ""),
                lead.get("email_verified", 0),
                json.dumps(lead.get("tech_stack", []), ensure_ascii=False),
                lead.get("status", "discovered"),
                lead.get("notes", ""),
            ))
            count += 1
        except Exception as e:
            print(f"     ⚠ DB error upserting lead '{lead.get('company_name', '?')}': {e}")
            continue

    conn.commit()
    log_activity("upserted_leads", f"Saved {count} leads to DB")
    conn.close()
    return count


def get_lead(lead_id: int) -> dict | None:
    """Get a single lead by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_status(lead_id: int, status: str, notes: str = ""):
    """Update lead pipeline status."""
    conn = get_connection()
    now = datetime.now().isoformat()

    updates = ["status = ?", "updated_at = ?"]
    params = [status, now]

    if status in ("contacted", "in_discussion"):
        updates.append("last_contacted_at = ?")
        params.append(now)

    if notes:
        updates.append("notes = CASE WHEN notes IS NULL OR notes = '' THEN ? ELSE notes || '\n' || ? END")
        params.extend([notes, notes])

    params.append(lead_id)
    conn.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    log_activity(f"lead_status_{status}", f"Lead {lead_id} → {status}" + (f": {notes}" if notes else ""))
    conn.close()


def update_lead_contact(lead_id: int, contact_name: str = "", contact_title: str = "",
                         contact_email: str = "", contact_linkedin: str = "",
                         email_verified: bool = False):
    """Update contact info for a lead."""
    conn = get_connection()
    conn.execute("""
        UPDATE leads SET
            contact_name = CASE WHEN ? != '' THEN ? ELSE contact_name END,
            contact_title = CASE WHEN ? != '' THEN ? ELSE contact_title END,
            contact_email = CASE WHEN ? != '' THEN ? ELSE contact_email END,
            contact_linkedin = CASE WHEN ? != '' THEN ? ELSE contact_linkedin END,
            email_verified = CASE WHEN ? THEN 1 ELSE email_verified END,
            updated_at = datetime('now')
        WHERE id = ?
    """, (
        contact_name, contact_name,
        contact_title, contact_title,
        contact_email, contact_email,
        contact_linkedin, contact_linkedin,
        email_verified,
        lead_id,
    ))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  OUTREACH ATTEMPTS CRUD
# ══════════════════════════════════════════════════════════════════════════

def create_outreach_attempt(lead_id: int, channel: str, message_type: str,
                             subject: str = "", body: str = "",
                             generated_by: str = "llm_auto") -> int:
    """Create a draft outreach attempt. Returns the new attempt ID."""
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO outreach_attempts (lead_id, channel, message_type, subject, body, generated_by, status)
        VALUES (?, ?, ?, ?, ?, ?, 'draft')
    """, (lead_id, channel, message_type, subject, body, generated_by))
    conn.commit()
    attempt_id = cur.lastrowid
    log_activity("outreach_draft", f"Draft {message_type} for lead {lead_id} via {channel}")
    conn.close()
    return attempt_id


def approve_outreach(attempt_id: int, edited_body: str = "") -> bool:
    """Approve a draft outreach attempt (marks as 'approved' for sending).
    If edited_body is provided, updates the body first."""
    conn = get_connection()

    if edited_body:
        conn.execute("UPDATE outreach_attempts SET body = ? WHERE id = ? AND status = 'draft'",
                     (edited_body, attempt_id))

    result = conn.execute("""
        UPDATE outreach_attempts SET status = 'approved', sent_at = datetime('now')
        WHERE id = ? AND status IN ('draft', 'approved')
    """, (attempt_id,))
    conn.commit()

    if result.rowcount > 0:
        log_activity("outreach_approved", f"Approved outreach attempt {attempt_id}")
    conn.close()
    return result.rowcount > 0


def reject_outreach(attempt_id: int) -> bool:
    """Reject a draft outreach attempt."""
    conn = get_connection()
    result = conn.execute("""
        UPDATE outreach_attempts SET status = 'rejected'
        WHERE id = ? AND status = 'draft'
    """, (attempt_id,))
    conn.commit()
    if result.rowcount > 0:
        log_activity("outreach_rejected", f"Rejected outreach attempt {attempt_id}")
    conn.close()
    return result.rowcount > 0


def mark_outreach_sent(attempt_id: int, metadata: dict | None = None) -> bool:
    """Mark an outreach attempt as sent (called after actual send succeeds)."""
    conn = get_connection()
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    result = conn.execute("""
        UPDATE outreach_attempts SET
            status = 'sent',
            sent_at = datetime('now'),
            metadata = CASE WHEN ? IS NOT NULL THEN ? ELSE metadata END
        WHERE id = ? AND status IN ('approved', 'draft')
    """, (meta_json, meta_json, attempt_id))

    # Also update lead's last_contacted_at
    if result.rowcount > 0:
        attempt = conn.execute("SELECT lead_id FROM outreach_attempts WHERE id = ?",
                               (attempt_id,)).fetchone()
        if attempt:
            conn.execute("UPDATE leads SET last_contacted_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                         (attempt["lead_id"],))

    conn.commit()
    conn.close()
    return result.rowcount > 0


def mark_outreach_failed(attempt_id: int, error: str = "") -> bool:
    """Mark an outreach attempt as failed."""
    conn = get_connection()
    result = conn.execute("""
        UPDATE outreach_attempts SET status = 'failed',
            response_text = CASE WHEN ? != '' THEN ? ELSE response_text END
        WHERE id = ?
    """, (error, error, attempt_id))
    conn.commit()
    conn.close()
    return result.rowcount > 0


def record_response(attempt_id: int, response_text: str):
    """Record a response to an outreach attempt."""
    conn = get_connection()
    conn.execute("""
        UPDATE outreach_attempts SET
            status = 'replied',
            response_text = ?,
            responded_at = datetime('now')
        WHERE id = ?
    """, (response_text, attempt_id))
    conn.commit()
    log_activity("outreach_response", f"Response received for attempt {attempt_id}")
    conn.close()


def get_pending_approvals() -> list[dict]:
    """Get all outreach attempts awaiting approval with full lead context."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT oa.*,
               l.company_name, l.industry, l.company_size, l.location, l.website,
               l.signal_source, l.signal_type, l.signal_strength, l.signal_data,
               l.score_total, l.score_budget, l.score_need, l.score_accessibility, l.score_techfit,
               l.contact_name, l.contact_title, l.contact_linkedin, l.contact_email,
               l.tech_stack, l.status as lead_status, l.notes, l.discovered_at
        FROM outreach_attempts oa
        JOIN leads l ON oa.lead_id = l.id
        WHERE oa.status = 'draft'
        ORDER BY l.score_total DESC, oa.created_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_outreach_for_lead(lead_id: int) -> list[dict]:
    """Get all outreach attempts for a lead, ordered by created_at."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM outreach_attempts
        WHERE lead_id = ?
        ORDER BY created_at ASC
    """, (lead_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
#  DEALS CRUD
# ══════════════════════════════════════════════════════════════════════════

def create_deal(lead_id: int, title: str, estimated_value_usd: float = 0,
                estimated_duration_months: int = 3, probability_pct: float = 10) -> int:
    """Create a new deal for a lead. Returns deal ID."""
    ev = estimated_value_usd * probability_pct / 100
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO deals (lead_id, title, estimated_value_usd, estimated_duration_months,
                           probability_pct, expected_value)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (lead_id, title, estimated_value_usd, estimated_duration_months, probability_pct, ev))
    conn.commit()
    deal_id = cur.lastrowid
    log_activity("deal_created", f"Deal '{title}' for lead {lead_id} (EV: ${ev:.0f})")
    conn.close()
    return deal_id


def update_deal_stage(deal_id: int, stage: str, closed_reason: str = ""):
    """Update deal stage."""
    conn = get_connection()
    now = datetime.now().isoformat()
    closed_at = now if stage in ("closed_won", "closed_lost") else None

    conn.execute("""
        UPDATE deals SET stage = ?, closed_at = ?, closed_reason = ?, updated_at = ?
        WHERE id = ?
    """, (stage, closed_at, closed_reason, now, deal_id))
    conn.commit()
    log_activity(f"deal_{stage}", f"Deal {deal_id} → {stage}")
    conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  STATS & QUERIES
# ══════════════════════════════════════════════════════════════════════════

def get_stats(campaign_id: int = 0) -> dict:
    """Get pipeline statistics for dashboard. Optionally filter by campaign_id."""
    conn = get_connection()

    # Build WHERE clause for campaign filter
    lead_where = ""
    outreach_where = ""
    lead_params: list = []
    outreach_params: list = []

    if campaign_id > 0:
        lead_where = " WHERE campaign_id = ?"
        lead_params = [campaign_id]
        outreach_where = " WHERE oa.lead_id IN (SELECT id FROM leads WHERE campaign_id = ?)"
        outreach_params = [campaign_id]

    # Lead stats
    lead_stats = dict(conn.execute(f"""
        SELECT
            COUNT(*) as total_leads,
            COALESCE(SUM(CASE WHEN status = 'discovered' THEN 1 ELSE 0 END), 0) as discovered,
            COALESCE(SUM(CASE WHEN status = 'qualified' THEN 1 ELSE 0 END), 0) as qualified,
            COALESCE(SUM(CASE WHEN status = 'contacted' THEN 1 ELSE 0 END), 0) as contacted,
            COALESCE(SUM(CASE WHEN status = 'in_discussion' THEN 1 ELSE 0 END), 0) as in_discussion,
            COALESCE(SUM(CASE WHEN status = 'proposal_sent' THEN 1 ELSE 0 END), 0) as proposal_sent,
            COALESCE(SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END), 0) as won,
            COALESCE(SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END), 0) as lost,
            COALESCE(SUM(CASE WHEN score_total >= 80 THEN 1 ELSE 0 END), 0) as hot_leads,
            COALESCE(SUM(CASE WHEN score_total >= 60 AND score_total < 80 THEN 1 ELSE 0 END), 0) as warm_leads,
            COALESCE(SUM(CASE WHEN score_total >= 40 AND score_total < 60 THEN 1 ELSE 0 END), 0) as cold_leads,
            ROUND(COALESCE(AVG(score_total), 0), 1) as avg_score,
            COALESCE(MAX(score_total), 0) as max_score
        FROM leads{lead_where}
    """, lead_params).fetchone())

    # Outreach stats
    if campaign_id > 0:
        outreach_stats = dict(conn.execute("""
            SELECT
                COUNT(*) as total_attempts,
                COALESCE(SUM(CASE WHEN oa.status = 'draft' THEN 1 ELSE 0 END), 0) as pending_approval,
                COALESCE(SUM(CASE WHEN oa.status = 'sent' THEN 1 ELSE 0 END), 0) as sent,
                COALESCE(SUM(CASE WHEN oa.status = 'replied' THEN 1 ELSE 0 END), 0) as replied,
                COALESCE(SUM(CASE WHEN oa.status = 'failed' THEN 1 ELSE 0 END), 0) as failed,
                COALESCE(SUM(CASE WHEN oa.status = 'rejected' THEN 1 ELSE 0 END), 0) as rejected_approval,
                COALESCE(SUM(CASE WHEN oa.direction = 'inbound' THEN 1 ELSE 0 END), 0) as inbound
            FROM outreach_attempts oa
            JOIN leads l ON oa.lead_id = l.id
            WHERE l.campaign_id = ?
        """, (campaign_id,)).fetchone())
    else:
        outreach_stats = dict(conn.execute("""
            SELECT
                COUNT(*) as total_attempts,
                COALESCE(SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END), 0) as pending_approval,
                COALESCE(SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END), 0) as sent,
                COALESCE(SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END), 0) as replied,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed,
                COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) as rejected_approval,
                COALESCE(SUM(CASE WHEN direction = 'inbound' THEN 1 ELSE 0 END), 0) as inbound
            FROM outreach_attempts
        """).fetchone())

    # Response rate
    total_sent = (outreach_stats.get("sent", 0) or 0)
    replied = (outreach_stats.get("replied", 0) or 0)
    outreach_stats["response_rate"] = round(replied / total_sent * 100, 1) if total_sent > 0 else 0

    # Outreach by channel
    by_channel = [dict(r) for r in conn.execute("""
        SELECT channel, COUNT(*) as total,
               SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent,
               SUM(CASE WHEN status = 'replied' THEN 1 ELSE 0 END) as replied
        FROM outreach_attempts
        GROUP BY channel
        ORDER BY total DESC
    """).fetchall()]

    # Pipeline value
    deal_value = conn.execute("""
        SELECT
            COUNT(*) as total_deals,
            COALESCE(SUM(estimated_value_usd), 0) as total_value,
            COALESCE(SUM(expected_value), 0) as total_expected_value,
            COALESCE(SUM(CASE WHEN stage = 'closed_won' THEN estimated_value_usd ELSE 0 END), 0) as won_value
        FROM deals
    """).fetchone()

    # Leads by industry
    by_industry = [dict(r) for r in conn.execute("""
        SELECT industry, COUNT(*) as count
        FROM leads WHERE industry IS NOT NULL AND industry != ''
        GROUP BY industry ORDER BY count DESC LIMIT 10
    """).fetchall()]

    # Leads by source
    by_source = [dict(r) for r in conn.execute("""
        SELECT signal_source as fuente, COUNT(*) as count
        FROM leads GROUP BY signal_source ORDER BY count DESC
    """).fetchall()]

    # Daily discovery trend (last 30 days)
    daily = [dict(r) for r in conn.execute("""
        SELECT date(discovered_at) as day, COUNT(*) as count
        FROM leads WHERE discovered_at >= date('now', '-30 days')
        GROUP BY day ORDER BY day
    """).fetchall()]

    # Campaigns breakdown
    all_campaigns = [dict(r) for r in conn.execute("""
        SELECT c.*, COUNT(l.id) as lead_count
        FROM campaigns c
        LEFT JOIN leads l ON c.id = l.campaign_id
        WHERE c.is_active = 1
        GROUP BY c.id
        ORDER BY c.created_at DESC
    """).fetchall()]

    conn.close()

    return {
        "leads": lead_stats,
        "outreach": outreach_stats,
        "outreach_by_channel": by_channel,
        "deals": dict(deal_value),
        "by_industry": by_industry,
        "by_source": by_source,
        "daily_discovery": daily,
        "campaigns": all_campaigns,
    }


# Whitelist for sort columns (prevents SQL injection)
ALLOWED_SORT_COLUMNS = {
    "score_total", "score_budget", "score_need", "score_accessibility", "score_techfit",
    "company_name", "industry", "signal_source", "status", "location",
    "discovered_at", "last_contacted_at", "updated_at",
}
ALLOWED_SORT_ORDERS = {"ASC", "DESC"}


def get_leads(
    search: str = "",
    status: str = "all",
    min_score: float = 0.0,
    max_score: float = 100.0,
    industry: str = "",
    signal_source: str = "",
    signal_type: str = "",
    location: str = "",
    campaign_id: int = 0,
    date_from: str = "",
    date_to: str = "",
    sort_by: str = "score_total",
    sort_order: str = "DESC",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Get leads with full filtering. Returns (rows, total_count) for pagination."""
    conn = get_connection()

    # Whitelist sort
    if sort_by not in ALLOWED_SORT_COLUMNS:
        sort_by = "score_total"
    if sort_order.upper() not in ALLOWED_SORT_ORDERS:
        sort_order = "DESC"

    where_clauses = []
    params: list = []

    if search.strip():
        pattern = f"%{search.strip()}%"
        where_clauses.append("(company_name LIKE ? OR industry LIKE ? OR contact_name LIKE ? OR notes LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern])

    if status and status != "all":
        where_clauses.append("status = ?")
        params.append(status)

    if min_score > 0:
        where_clauses.append("score_total >= ?")
        params.append(min_score)
    if max_score < 100:
        where_clauses.append("score_total <= ?")
        params.append(max_score)

    if industry.strip():
        where_clauses.append("industry = ?")
        params.append(industry.strip())

    if signal_source.strip():
        where_clauses.append("signal_source = ?")
        params.append(signal_source.strip())

    if signal_type.strip():
        where_clauses.append("signal_type = ?")
        params.append(signal_type.strip())

    if location.strip():
        where_clauses.append("location = ?")
        params.append(location.strip())

    if campaign_id > 0:
        where_clauses.append("campaign_id = ?")
        params.append(campaign_id)

    if date_from:
        where_clauses.append("discovered_at >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("discovered_at <= ?")
        params.append(date_to)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Count
    count_sql = f"SELECT COUNT(*) as total FROM leads {where_sql}"
    total = conn.execute(count_sql, params).fetchone()["total"]

    # Fetch
    query = f"""
        SELECT * FROM leads
        {where_sql}
        ORDER BY {sort_by} {sort_order}
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(query, params + [limit, offset]).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_leads_for_outreach(min_score: float = 50, limit: int = 20) -> list[dict]:
    """Get qualified leads that are ready for outreach (no pending/sent attempts)."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT l.* FROM leads l
        WHERE l.score_total >= ?
          AND l.status IN ('discovered', 'qualified')
          AND l.id NOT IN (
              SELECT DISTINCT lead_id FROM outreach_attempts
              WHERE status IN ('draft', 'approved', 'sent')
          )
        ORDER BY l.score_total DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_leads_needing_followup(days_no_response: int = 7) -> list[dict]:
    """Get leads that were contacted but haven't had a response in N days."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT l.*, MAX(oa.sent_at) as last_outreach
        FROM leads l
        JOIN outreach_attempts oa ON l.id = oa.lead_id
        WHERE l.status IN ('contacted', 'in_discussion')
          AND oa.status = 'sent'
          AND oa.sent_at < datetime('now', ?)
          AND l.id NOT IN (
              SELECT DISTINCT lead_id FROM outreach_attempts
              WHERE status = 'replied'
          )
        GROUP BY l.id
        ORDER BY l.score_total DESC
    """, (f"-{days_no_response} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
#  ACTIVITY LOG
# ══════════════════════════════════════════════════════════════════════════

def log_activity(action: str, details: str = ""):
    """Log an activity entry."""
    try:
        conn = get_connection()
        conn.execute("INSERT INTO activity_log (action, details) VALUES (?, ?)",
                     (action, details))
        conn.commit()
        conn.close()
    except Exception:
        pass  # Don't let logging failures break the main flow


def get_recent_activity(limit: int = 50) -> list[dict]:
    """Get recent activity log entries."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM activity_log
        ORDER BY timestamp DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════
#  CONFIGURATION (same pattern as auto_apply_config in jobs_db)
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "min_score_to_outreach": 50,
    "max_outreach_per_day": 10,
    "outreach_schedule": {
        "days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "start_hour_clt": 9,
        "end_hour_clt": 18,
    },
    "target_industries": [
        "fintech", "healthtech", "logistics", "ecommerce",
        "saas", "insurance", "real estate", "manufacturing",
        "legaltech", "edtech", "proptech",
    ],
    "platform_limits": {
        "linkedin_connect": {"max_per_day": 20, "max_per_hour": 5, "min_delay_s": 60, "max_delay_s": 180},
        "linkedin_dm": {"max_per_day": 10, "max_per_hour": 3, "min_delay_s": 120, "max_delay_s": 300},
        "email": {"max_per_day": 30, "max_per_hour": 10, "min_delay_s": 120, "max_delay_s": 300},
        "upwork": {"max_per_day": 5, "max_per_hour": 2, "min_delay_s": 180, "max_delay_s": 600},
        "contact_form": {"max_per_day": 10, "max_per_hour": 3, "min_delay_s": 90, "max_delay_s": 240},
    },
    "email_warmup_enabled": True,
    "email_warmup_week": 1,  # 1-4, increases daily limit
}


def get_config() -> dict:
    """Get the full configuration, merging stored values with defaults."""
    conn = get_connection()
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # Deep copy

    rows = conn.execute("SELECT key, value FROM scout_config").fetchall()
    conn.close()

    for r in rows:
        try:
            val = json.loads(r["value"])
            if r["key"] in config and isinstance(config[r["key"]], dict) and isinstance(val, dict):
                config[r["key"]].update(val)
            else:
                config[r["key"]] = val
        except (json.JSONDecodeError, TypeError):
            config[r["key"]] = r["value"]

    return config


def save_config(updates: dict) -> dict:
    """Save configuration updates. Returns full config after save."""
    conn = get_connection()
    for key, value in updates.items():
        json_val = json.dumps(value, ensure_ascii=False)
        conn.execute("""
            INSERT INTO scout_config (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, json_val))
    conn.commit()
    conn.close()
    return get_config()


# ══════════════════════════════════════════════════════════════════════════
#  CAMPAIGNS CRUD
# ══════════════════════════════════════════════════════════════════════════

def create_campaign(name: str, description: str = "",
                    target_countries: list | None = None,
                    target_industries: list | None = None,
                    target_services: list | None = None) -> int:
    """Create a new campaign. Returns campaign ID."""
    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO campaigns (name, description, target_countries, target_industries, target_services)
        VALUES (?, ?, ?, ?, ?)
    """, (
        name, description,
        json.dumps(target_countries or [], ensure_ascii=False),
        json.dumps(target_industries or [], ensure_ascii=False),
        json.dumps(target_services or [], ensure_ascii=False),
    ))
    conn.commit()
    cid = cur.lastrowid
    log_activity("campaign_created", f"Campaign '{name}' created (id={cid})")
    conn.close()
    return cid


def update_campaign(campaign_id: int, **kwargs) -> bool:
    """Update campaign fields. Accepts: name, description, target_countries,
    target_industries, target_services, is_active."""
    conn = get_connection()
    fields = []
    params = []

    for key in ["name", "description"]:
        if key in kwargs and kwargs[key] is not None:
            fields.append(f"{key} = ?")
            params.append(kwargs[key])

    for key in ["target_countries", "target_industries", "target_services"]:
        if key in kwargs and kwargs[key] is not None:
            fields.append(f"{key} = ?")
            params.append(json.dumps(kwargs[key], ensure_ascii=False))

    if "is_active" in kwargs:
        fields.append("is_active = ?")
        params.append(1 if kwargs["is_active"] else 0)

    if not fields:
        conn.close()
        return False

    fields.append("updated_at = datetime('now')")
    params.append(campaign_id)

    conn.execute(f"UPDATE campaigns SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return True


def delete_campaign(campaign_id: int) -> bool:
    """Soft-delete a campaign (set is_active=0)."""
    conn = get_connection()
    conn.execute("UPDATE campaigns SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
                 (campaign_id,))
    conn.commit()
    affected = conn.total_changes
    conn.close()
    return affected > 0


def get_campaigns(active_only: bool = True) -> list[dict]:
    """List campaigns."""
    conn = get_connection()
    where = "WHERE is_active = 1" if active_only else ""
    rows = conn.execute(f"SELECT * FROM campaigns {where} ORDER BY created_at DESC").fetchall()

    # Enrich with lead counts
    result = []
    for r in rows:
        d = dict(r)
        count_row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE campaign_id = ?",
                                 (d["id"],)).fetchone()
        d["lead_count"] = count_row["c"] if count_row else 0
        result.append(d)

    conn.close()
    return result


def get_campaign(campaign_id: int) -> dict | None:
    """Get a single campaign by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if row:
        d = dict(row)
        count_row = conn.execute("SELECT COUNT(*) as c FROM leads WHERE campaign_id = ?",
                                 (campaign_id,)).fetchone()
        d["lead_count"] = count_row["c"] if count_row else 0
        conn.close()
        return d
    conn.close()
    return None


def assign_lead_to_campaign(lead_id: int, campaign_id: int | None) -> bool:
    """Assign (or unassign) a lead to a campaign. campaign_id=None to remove."""
    conn = get_connection()
    conn.execute("UPDATE leads SET campaign_id = ?, updated_at = datetime('now') WHERE id = ?",
                 (campaign_id, lead_id))
    conn.commit()
    conn.close()
    return True


def get_distinct_locations() -> list[str]:
    """Get list of distinct location values for filter dropdowns."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT location FROM leads WHERE location IS NOT NULL AND location != '' ORDER BY location"
    ).fetchall()
    conn.close()
    # Extract country/region from location strings
    locations = []
    for r in rows:
        loc = r["location"]
        if loc:
            locations.append(loc)
    return locations


# ══════════════════════════════════════════════════════════════════════════
#  DATA EXPORT
# ══════════════════════════════════════════════════════════════════════════

def export_leads_json(limit: int = 100, min_score: float = 0) -> str:
    """Export leads to JSON string for dashboard API."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT company_name, industry, company_size, location, website,
               signal_source, signal_type, score_total, status,
               contact_name, contact_title, discovered_at
        FROM leads
        WHERE score_total >= ?
        ORDER BY score_total DESC
        LIMIT ?
    """, (min_score, limit)).fetchall()
    conn.close()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)


# Initialize on import
init_db()
