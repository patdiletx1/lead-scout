#!/usr/bin/env python3
"""
ClientScout Dashboard — Flask server for B2B lead pipeline management.
Runs on :5004, served by Caddy at leads.patdilet.dev

Features:
  - Stats cards (total leads, pipeline stages, response rate)
  - Lead table with filters, sorting, pagination
  - Score distribution and source breakdown charts (Chart.js)
  - Approval Queue for reviewing outreach drafts (Phase 2)
  - Modal with lead detail and score breakdown
  - AJAX actions, toast notifications, keyboard shortcuts

Follows the same architecture as dashboard.py (jobs.patdilet.dev).
"""

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template_string, request, abort

# Add parent dir for imports (works both locally and on VPS)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leads_db import (
    get_stats, get_leads, get_lead, update_lead_status, update_lead_contact,
    get_pending_approvals, get_outreach_for_lead,
    approve_outreach, reject_outreach, mark_outreach_sent, mark_outreach_failed,
    create_outreach_attempt,
    get_recent_activity, get_config, save_config, log_activity,
    get_connection, DB_PATH,
    create_campaign, update_campaign, delete_campaign, get_campaigns, get_campaign,
    assign_lead_to_campaign, get_distinct_locations,
)

app = Flask(__name__)


# ── Jinja helpers ──────────────────────────────────────────────────────

@app.template_filter("from_json")
def from_json_filter(s):
    """Parse a JSON string in the template. Returns empty list/dict on failure."""
    if not s:
        return {}
    try:
        result = json.loads(s)
        if isinstance(result, str):
            result = json.loads(result)
        return result if isinstance(result, (dict, list)) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


@app.context_processor
def utility_processor():
    def pagination_url(page):
        args = request.args.copy()
        args["page"] = str(page)
        return "?" + "&".join(f"{k}={v}" for k, v in args.items())

    def interpret_signal(lead: dict) -> dict:
        """Interpret a lead's signal into human-readable context and service recommendations."""
        signal_type = (lead.get("signal_type") or "")
        industry = (lead.get("industry") or "technology")
        signal_data = lead.get("signal_data", {})
        if isinstance(signal_data, str):
            try:
                signal_data = json.loads(signal_data)
            except Exception:
                signal_data = {}

        result = {"why": "", "offer": [], "urgency": "medium"}

        if signal_type == "hiring_cto":
            result["why"] = "Sin liderazgo tecnico. Suelen necesitar apoyo externo para mantener el desarrollo mientras buscan CTO permanente — o deciden tercerizar completamente."
            result["offer"] = ["Desarrollo software a medida (interim tech leadership)", "Automatizacion de procesos internos", "Consultoria de arquitectura cloud"]
            result["urgency"] = "high"
        elif signal_type == "hiring_spree":
            result["why"] = "Multiples roles tech abiertos = estan escalando rapido. Tienen mas demanda que capacidad interna. Necesitan overflow de desarrollo."
            result["offer"] = ["Backend .NET/Python como extension de equipo", "Automatizacion CI/CD y procesos", "Integracion de APIs y sistemas"]
            result["urgency"] = "high"
        elif signal_type == "transformation":
            result["why"] = "Transformacion digital en marcha. Presupuesto asignado, estan evaluando soluciones. Momento ideal para ofrecer automatizacion."
            result["offer"] = ["Automatizacion de procesos end-to-end (RPA)", "Integracion AI/LLMs", "Modernizacion legacy a cloud"]
            result["urgency"] = "high"
        elif signal_type == "legacy_modernization":
            result["why"] = "Migrando sistemas legacy. Necesitan devs que entiendan stacks antiguos Y modernos — perfil dificil de encontrar."
            result["offer"] = ["Migracion legacy a .NET/Azure", "Re-arquitectura monolitos a microservicios", "Automatizacion para reemplazar sistemas viejos"]
        elif signal_type == "automation_need":
            result["why"] = "Necesidad explicita de automatizacion. Ya saben lo que quieren — solo necesitan quien lo ejecute."
            result["offer"] = ["Web scraping y extraccion de datos", "RPA para procesos manuales", "Integracion de APIs y sincronizacion"]
            result["urgency"] = "high"
        elif signal_type == "project_post":
            budget = signal_data.get("project_budget", 0)
            result["why"] = f"Proyecto publicado. Presupuesto est. ${budget}. El cliente ya esta listo para contratar."
            result["offer"] = ["Desarrollo del proyecto segun specs", "Consultoria para refinar alcance", "Mantenimiento post-entrega"]
            result["urgency"] = "high"
        elif signal_type == "funding":
            result["why"] = "Recibieron financiamiento. Presupuesto fresco y mandato de crecer. Invierten en tecnologia inmediatamente."
            result["offer"] = ["Desarrollo de MVP o escalamiento", "Automatizacion de operaciones", "Integraciones con sistemas existentes"]
        else:
            result["why"] = f"Senal detectada en industria {industry}."
            result["offer"] = ["Automatizacion de procesos", "Desarrollo de software a medida", "Integracion AI/LLMs"]

        return result

    return dict(pagination_url=pagination_url, interpret_signal=interpret_signal)


# ══════════════════════════════════════════════════════════════════════════
#  HTML TEMPLATE (inline — single-file deployment)
# ══════════════════════════════════════════════════════════════════════════

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ClientScout — Patricio Díaz</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <style>
        :root {
            --bg: #0d1117; --card: #161b22; --border: #30363d;
            --text: #c9d1d9; --text-dim: #8b949e; --accent: #58a6ff;
            --green: #3fb950; --yellow: #d29922; --red: #f85149;
            --purple: #bc8cff; --cyan: #79c0ff; --orange: #d29922;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 24px; }
        .container { max-width: 1400px; margin: 0 auto; }

        /* Header */
        .header-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; flex-wrap: wrap; gap: 8px; }
        h1 { font-size: 28px; }
        .subtitle { color: var(--text-dim); font-size: 13px; }
        .refresh-btn { background: none; border: 1px solid var(--border); border-radius: 4px; color: var(--text-dim); cursor: pointer; padding: 5px 10px; font-size: 14px; }
        .refresh-btn:hover { color: var(--text); border-color: var(--accent); }

        /* Section titles */
        h2 { font-size: 18px; margin: 24px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
        h2 .badge { font-size: 12px; padding: 2px 8px; border-radius: 12px; margin-left: 8px; vertical-align: middle; }

        /* Search */
        .search-bar { display: flex; gap: 8px; margin: 16px 0; }
        .search-bar input { flex: 1; padding: 8px 14px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 14px; }
        .search-bar input:focus { outline: none; border-color: var(--accent); }
        .search-bar .result-count { font-size: 12px; color: var(--text-dim); align-self: center; white-space: nowrap; }

        /* Stats */
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
        .stat-card .value { font-size: 26px; font-weight: 700; }
        .stat-card .label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
        .stat-card .value.green { color: var(--green); }
        .stat-card .value.yellow { color: var(--yellow); }
        .stat-card .value.red { color: var(--red); }
        .stat-card .value.blue { color: var(--accent); }
        .stat-card .value.purple { color: var(--purple); }
        .stat-card .value.cyan { color: var(--cyan); }

        /* Pipeline bar */
        .pipeline-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 8px; }
        .pipeline-bar .seg { transition: width 0.5s ease; }
        .pipeline-bar .seg.discovered { background: var(--accent); }
        .pipeline-bar .seg.qualified { background: var(--cyan); }
        .pipeline-bar .seg.contacted { background: var(--yellow); }
        .pipeline-bar .seg.discussion { background: var(--orange); }
        .pipeline-bar .seg.proposal { background: var(--purple); }
        .pipeline-bar .seg.won { background: var(--green); }
        .pipeline-bar .seg.lost { background: var(--red); }

        /* Charts */
        .charts-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; margin-bottom: 20px; }
        .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
        .chart-card h3 { font-size: 13px; color: var(--text-dim); margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
        .chart-card canvas { max-height: 250px; }
        @media (max-width: 768px) { .charts-row { grid-template-columns: 1fr; } }

        /* Filters */
        .filter-section { margin-bottom: 14px; }
        .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; align-items: center; }
        .filters a { padding: 5px 14px; border-radius: 20px; background: var(--card); border: 1px solid var(--border); color: var(--text); text-decoration: none; font-size: 12px; white-space: nowrap; }
        .filters a.active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .filters a:hover:not(.active) { border-color: var(--accent); }
        .filter-select { padding: 5px 10px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 12px; cursor: pointer; }
        .filter-select:focus { border-color: var(--accent); outline: none; }
        .filter-input { width: 90px; padding: 5px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 12px; text-align: center; }
        .filter-input:focus { outline: none; border-color: var(--accent); }

        /* Table */
        .table-wrapper { overflow-x: auto; }
        .lead-table { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 1100px; }
        .lead-table th { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--border); color: var(--text-dim); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; cursor: pointer; user-select: none; }
        .lead-table th:hover { color: var(--text); }
        .lead-table th .sort-arrow { font-size: 9px; margin-left: 3px; opacity: 0.4; }
        .lead-table th .sort-arrow.active { opacity: 1; color: var(--accent); }
        .lead-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        .lead-table tr:hover { background: rgba(88,166,255,0.06); }
        .lead-table a { color: var(--accent); text-decoration: none; }
        .lead-table a:hover { text-decoration: underline; }

        /* Badges */
        .score-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-weight: 600; font-size: 12px; white-space: nowrap; min-width: 32px; text-align: center; }
        .score-hot { background: rgba(63,185,80,0.15); color: var(--green); }
        .score-warm { background: rgba(210,153,34,0.15); color: var(--yellow); }
        .score-cold { background: rgba(139,148,158,0.15); color: var(--text-dim); }
        .score-discard { background: rgba(248,81,73,0.15); color: var(--red); }
        .status-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 10px; white-space: nowrap; }
        .status-discovered { background: rgba(88,166,255,0.15); color: var(--accent); }
        .status-qualified { background: rgba(121,192,255,0.15); color: var(--cyan); }
        .status-contacted { background: rgba(210,153,34,0.15); color: var(--yellow); }
        .status-discussion { background: rgba(210,153,34,0.2); color: var(--orange); }
        .status-proposal { background: rgba(188,140,255,0.15); color: var(--purple); }
        .status-won { background: rgba(63,185,80,0.15); color: var(--green); }
        .status-lost { background: rgba(248,81,73,0.15); color: var(--red); }
        .source-badge { color: var(--text-dim); font-size: 11px; }
        .signal-badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px; }
        .signal-strong { background: rgba(63,185,80,0.12); color: var(--green); }
        .signal-medium { background: rgba(210,153,34,0.12); color: var(--yellow); }
        .signal-weak { background: rgba(139,148,158,0.12); color: var(--text-dim); }

        /* Action buttons */
        .action-btn { padding: 4px 9px; border-radius: 4px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; font-size: 11px; white-space: nowrap; }
        .action-btn:hover { border-color: var(--accent); }
        .action-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .action-btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
        .action-btn.primary:hover { opacity: 0.85; }
        .action-btn.approve-btn { border-color: var(--green); color: var(--green); }
        .action-btn.approve-btn:hover { background: rgba(63,185,80,0.1); }
        .action-btn.reject-btn { border-color: var(--red); color: var(--red); }
        .action-btn.reject-btn:hover { background: rgba(248,81,73,0.1); }

        /* Rich Approval Card */
        .rich-approval-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 0; margin-bottom: 16px; overflow: hidden; }
        .rich-approval-card:hover { border-color: var(--accent); }
        .rac-header { padding: 14px 18px 10px; border-bottom: 1px solid var(--border); background: rgba(88,166,255,0.03); }
        .rac-company { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
        .rac-company-name { font-weight: 700; font-size: 16px; color: var(--text); }
        .rac-meta-top { font-size: 11px; color: var(--text-dim); }
        .urgency-badge { font-size: 10px; padding: 2px 8px; border-radius: 10px; }
        .urgency-badge.high { background: rgba(248,81,73,0.15); color: var(--red); }
        .rac-body { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0; }
        @media (max-width: 900px) { .rac-body { grid-template-columns: 1fr; } }
        .rac-col { padding: 14px 18px; border-right: 1px solid var(--border); }
        .rac-col:last-child { border-right: none; }
        .rac-col-title { font-size: 11px; font-weight: 600; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
        .rac-col-content { font-size: 13px; color: var(--text); line-height: 1.55; }
        .rac-detail-row { margin-top: 8px; font-size: 12px; }
        .rac-detail-label { color: var(--text-dim); font-weight: 500; }
        .rac-offer-list { margin: 0; padding-left: 16px; font-size: 13px; line-height: 1.7; }
        .rac-offer-list li { margin-bottom: 2px; }
        .rac-offer-list li::marker { color: var(--green); }
        /* Score bars */
        .rac-score-grid { display: flex; flex-direction: column; gap: 6px; }
        .rac-score-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
        .rac-score-label { width: 50px; color: var(--text-dim); text-align: right; }
        .rac-score-bar-bg { flex: 1; height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; }
        .rac-score-bar { display: block; height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }
        .rac-score-bar.need { background: var(--yellow); }
        .rac-score-bar.access { background: var(--green); }
        .rac-score-bar.techfit { background: var(--purple); }
        .rac-score-val { width: 35px; font-weight: 600; color: var(--text); text-align: right; }
        /* Message */
        .rac-message { padding: 14px 18px; border-top: 1px solid var(--border); background: var(--bg); }
        .rac-message-header { margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
        .rac-msg-type { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }
        .rac-msg-by { font-size: 10px; color: var(--text-dim); margin-left: auto; }
        .rac-msg-body { font-size: 14px; color: var(--text); line-height: 1.6; white-space: pre-wrap; font-style: italic; padding: 10px 14px; background: var(--card); border-radius: 6px; border-left: 3px solid var(--accent); }
        .channel-tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; background: rgba(88,166,255,0.1); color: var(--cyan); }
        /* Actions */
        .rac-actions { padding: 12px 18px; border-top: 1px solid var(--border); display: flex; gap: 8px; align-items: center; }

        /* Pagination */
        .pagination { display: flex; justify-content: center; align-items: center; gap: 4px; margin-top: 18px; flex-wrap: wrap; }
        .pagination a, .pagination span { padding: 6px 11px; border-radius: 4px; border: 1px solid var(--border); background: var(--card); color: var(--text); text-decoration: none; font-size: 12px; }
        .pagination a.active { background: var(--accent); color: #fff; border-color: var(--accent); }
        .pagination a:hover:not(.active):not(.disabled) { border-color: var(--accent); }
        .pagination a.disabled { opacity: 0.3; pointer-events: none; }
        .pagination .page-info { border: none; background: none; color: var(--text-dim); }
        .per-page select { padding: 5px 8px; border-radius: 4px; border: 1px solid var(--border); background: var(--card); color: var(--text); font-size: 12px; }

        /* Modal */
        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.65); z-index: 999; display: flex; align-items: center; justify-content: center; }
        .modal-content { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 24px; max-width: 680px; width: 92%; max-height: 85vh; overflow-y: auto; position: relative; }
        .modal-content h2 { font-size: 18px; margin: 0 0 8px; padding-right: 30px; border: none; }
        .modal-close { position: absolute; top: 12px; right: 16px; background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 22px; line-height: 1; }
        .modal-close:hover { color: var(--text); }
        .score-breakdown { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; margin: 12px 0; }
        .score-item { background: var(--bg); border-radius: 6px; padding: 8px 12px; }
        .score-item .score-label { font-size: 10px; color: var(--text-dim); text-transform: uppercase; }
        .score-item .score-val { font-size: 20px; font-weight: 700; }
        .modal-actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
        .modal-notes textarea, .modal-field input, .modal-field textarea { width: 100%; margin-top: 4px; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 10px; font-size: 12px; }
        .modal-field textarea { resize: vertical; min-height: 60px; }
        .modal-field textarea:focus, .modal-field input:focus { outline: none; border-color: var(--accent); }
        .modal-field label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.5px; }
        .modal-field { margin-bottom: 10px; }

        /* Toast */
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 2000; display: flex; flex-direction: column; gap: 8px; }
        .toast { padding: 10px 18px; border-radius: 6px; background: #21262d; border: 1px solid var(--border); color: var(--text); font-size: 13px; box-shadow: 0 4px 14px rgba(0,0,0,0.4); animation: slideIn 0.2s ease; }
        .toast.success { border-left: 3px solid var(--green); }
        .toast.error { border-left: 3px solid var(--red); }
        @keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }

        /* Footer */
        .last-updated { font-size: 11px; color: var(--text-dim); text-align: right; margin-top: 20px; }

        /* Empty state */
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-dim); }
        .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
        .empty-state p { margin-bottom: 8px; }
    </style>
</head>
<body>
<div class="container">

    {# ── Header ────────────────────────────────────────────────────── #}
    <div class="header-row">
        <div>
            <h1>🔍 ClientScout</h1>
            <p class="subtitle">
                Pipeline de prospección B2B — Patricio Díaz |
                {{ stats.leads.total_leads }} leads, {{ stats.outreach.pending_approval or 0 }} pendientes de aprobación
            </p>
        </div>
        <div style="display:flex;gap:8px;">
            <button class="action-btn primary" onclick="runDiscovery()" title="Buscar nuevas señales">🔎 Discover</button>
            <button class="action-btn" onclick="runEnrichment()" title="Enriquecer leads con emails y tech stack" style="border-color:var(--cyan);color:var(--cyan)">🔬 Enrich</button>
            <button class="action-btn" onclick="generateOutreach()" title="Generar mensajes de outreach para leads calificados" style="border-color:var(--purple);color:var(--purple)">✉️ Generate</button>
            <button class="refresh-btn" onclick="location.reload()">↻ Refrescar</button>
        </div>
    </div>

    {# ── Search bar ───────────────────────────────────────────────── #}
    <div class="search-bar">
        <input type="text" id="searchInput" placeholder="🔍 Buscar por empresa, industria, contacto... (atajo: /)"
               value="{{ search }}" onkeydown="if(event.key==='Enter')applyFilters()">
        <button class="action-btn" onclick="applyFilters()" style="font-size:13px;padding:8px 18px">Buscar</button>
        <span class="result-count">{{ total_leads }} resultado{{ 's' if total_leads != 1 else '' }}</span>
    </div>

    {# ── Stat cards ───────────────────────────────────────────────── #}
    <div class="stats-grid">
        <div class="stat-card">
            <div class="value blue">{{ stats.leads.total_leads }}</div>
            <div class="label">Total Leads</div>
        </div>
        <div class="stat-card">
            <div class="value blue">{{ stats.leads.discovered }}</div>
            <div class="label">Descubiertos</div>
        </div>
        <div class="stat-card">
            <div class="value cyan">{{ stats.leads.qualified }}</div>
            <div class="label">Calificados (60+)</div>
        </div>
        <div class="stat-card">
            <div class="value yellow">{{ stats.leads.contacted }}</div>
            <div class="label">Contactados</div>
        </div>
        <div class="stat-card">
            <div class="value purple">{{ stats.leads.in_discussion + stats.leads.proposal_sent }}</div>
            <div class="label">En Discusión / Prop.</div>
        </div>
        <div class="stat-card">
            <div class="value green">{{ stats.leads.hot_leads }}</div>
            <div class="label">Hot (80+)</div>
        </div>
        <div class="stat-card">
            <div class="value yellow">{{ stats.leads.warm_leads }}</div>
            <div class="label">Warm (60-79)</div>
        </div>
        <div class="stat-card">
            <div class="value" style="color:{{ 'var(--green)' if stats.outreach.response_rate > 5 else 'var(--yellow)' if stats.outreach.response_rate > 2 else 'var(--text-dim)' }}">
                {{ stats.outreach.response_rate }}%
            </div>
            <div class="label">Tasa Respuesta</div>
        </div>
        {% if stats.deals.total_value > 0 %}
        <div class="stat-card">
            <div class="value green">${{ '{:,.0f}'.format(stats.deals.total_value) }}</div>
            <div class="label">Pipeline Value</div>
        </div>
        {% endif %}
        <div class="stat-card">
            <div class="value purple">{{ stats.outreach.pending_approval or 0 }}</div>
            <div class="label">Pendientes Aprobación</div>
        </div>
    </div>

    {# ── Pipeline bar ─────────────────────────────────────────────── #}
    {% set stages = {
        'discovered': stats.leads.discovered, 'qualified': stats.leads.qualified,
        'contacted': stats.leads.contacted, 'in_discussion': stats.leads.in_discussion,
        'proposal_sent': stats.leads.proposal_sent, 'won': stats.leads.won, 'lost': stats.leads.lost
    } %}
    {% set pl_total = stages.values()|sum %}
    {% if pl_total > 0 %}
    <div class="chart-card" style="margin-bottom:18px">
        <h3>Pipeline</h3>
        <div class="pipeline-bar">
            <div class="seg discovered" style="width:{{ stages.discovered / pl_total * 100 }}%"></div>
            <div class="seg qualified" style="width:{{ stages.qualified / pl_total * 100 }}%"></div>
            <div class="seg contacted" style="width:{{ stages.contacted / pl_total * 100 }}%"></div>
            <div class="seg discussion" style="width:{{ stages.in_discussion / pl_total * 100 }}%"></div>
            <div class="seg proposal" style="width:{{ stages.proposal_sent / pl_total * 100 }}%"></div>
            <div class="seg won" style="width:{{ stages.won / pl_total * 100 }}%"></div>
            <div class="seg lost" style="width:{{ stages.lost / pl_total * 100 }}%"></div>
        </div>
        <p style="font-size:11px;color:var(--text-dim);margin-top:6px">
            <span style="color:var(--accent)">■</span> Descubiertos {{ stages.discovered }}
            <span style="color:var(--cyan);margin-left:8px">■</span> Calificados {{ stages.qualified }}
            <span style="color:var(--yellow);margin-left:8px">■</span> Contactados {{ stages.contacted }}
            <span style="color:var(--orange);margin-left:8px">■</span> En Discusión {{ stages.in_discussion }}
            <span style="color:var(--purple);margin-left:8px">■</span> Propuesta {{ stages.proposal_sent }}
            <span style="color:var(--green);margin-left:8px">■</span> Won {{ stages.won }}
            <span style="color:var(--red);margin-left:8px">■</span> Lost {{ stages.lost }}
        </p>
    </div>
    {% endif %}

    {# ── Charts ───────────────────────────────────────────────────── #}
    <div class="charts-row">
        <div class="chart-card">
            <h3>Distribución de Scores</h3>
            <canvas id="scoreChart"></canvas>
        </div>
        <div class="chart-card">
            <h3>Leads por Fuente</h3>
            <canvas id="sourceChart"></canvas>
        </div>
        {% if stats.by_industry %}
        <div class="chart-card">
            <h3>Top Industrias</h3>
            <canvas id="industryChart"></canvas>
        </div>
        {% endif %}
        <div class="chart-card">
            <h3>Outreach por Canal</h3>
            <canvas id="channelChart"></canvas>
        </div>
    </div>

    {# ── Approval Queue ───────────────────────────────────────────── #}
    {% if pending_approvals %}
    <h2>✅ Approval Queue <span class="badge status-proposal">{{ pending_approvals|length }} pendientes</span></h2>
    <div id="approvalQueue">
        {% for item in pending_approvals %}
        {% set ctx = interpret_signal(item) %}
        <div class="rich-approval-card" id="approval-{{ item.id }}">
            {# ── Header: Company + Score + Signal ─────────────────────── #}
            <div class="rac-header">
                <div class="rac-company">
                    <span class="rac-company-name">{{ item.company_name }}</span>
                    <span class="score-badge {{ 'score-hot' if item.score_total >= 80 else 'score-warm' if item.score_total >= 60 else 'score-cold' }}">{{ item.score_total|int }}</span>
                    {% if ctx.urgency == 'high' %}<span class="urgency-badge high">⚡ Alta prioridad</span>{% endif %}
                </div>
                <div class="rac-meta-top">
                    {{ item.industry or '—' }} · {{ item.company_size or '?' }} emp. · {{ item.location or 'Remote' }} ·
                    {{ item.signal_source }} · <span class="signal-badge signal-{{ item.signal_strength }}">{{ item.signal_type }}</span>
                </div>
            </div>

            {# ── Body: 3-column layout ──────────────────────────────── #}
            <div class="rac-body">
                {# Column 1: Why this lead? #}
                <div class="rac-col">
                    <div class="rac-col-title">🔍 Por que este lead</div>
                    <div class="rac-col-content">{{ ctx.why }}</div>
                    {% if item.signal_data %}
                    {% set sd = item.signal_data|from_json %}
                    {% if sd.job_titles and sd.job_titles|length > 0 %}
                    <div class="rac-detail-row">
                        <span class="rac-detail-label">Roles detectados:</span>
                        <span>{{ sd.job_titles[:3]|join(', ') }}</span>
                    </div>
                    {% endif %}
                    {% endif %}
                </div>

                {# Column 2: What to offer #}
                <div class="rac-col">
                    <div class="rac-col-title">🎯 Que ofrecer</div>
                    <div class="rac-col-content">
                        <ul class="rac-offer-list">
                            {% for offer in ctx.offer %}
                            <li>{{ offer }}</li>
                            {% endfor %}
                        </ul>
                    </div>
                </div>

                {# Column 3: Score breakdown #}
                <div class="rac-col">
                    <div class="rac-col-title">📊 Score</div>
                    <div class="rac-score-grid">
                        <div class="rac-score-row">
                            <span class="rac-score-label">Budget</span>
                            <span class="rac-score-bar-bg"><span class="rac-score-bar" style="width:{{ (item.score_budget or 0) / 30 * 100 }}%"></span></span>
                            <span class="rac-score-val">{{ item.score_budget|int }}/30</span>
                        </div>
                        <div class="rac-score-row">
                            <span class="rac-score-label">Need</span>
                            <span class="rac-score-bar-bg"><span class="rac-score-bar need" style="width:{{ (item.score_need or 0) / 35 * 100 }}%"></span></span>
                            <span class="rac-score-val">{{ item.score_need|int }}/35</span>
                        </div>
                        <div class="rac-score-row">
                            <span class="rac-score-label">Access</span>
                            <span class="rac-score-bar-bg"><span class="rac-score-bar access" style="width:{{ (item.score_accessibility or 0) / 20 * 100 }}%"></span></span>
                            <span class="rac-score-val">{{ item.score_accessibility|int }}/20</span>
                        </div>
                        <div class="rac-score-row">
                            <span class="rac-score-label">TechFit</span>
                            <span class="rac-score-bar-bg"><span class="rac-score-bar techfit" style="width:{{ (item.score_techfit or 0) / 15 * 100 }}%"></span></span>
                            <span class="rac-score-val">{{ item.score_techfit|int }}/15</span>
                        </div>
                    </div>
                </div>
            </div>

            {# ── Message section ─────────────────────────────────────── #}
            <div class="rac-message">
                <div class="rac-message-header">
                    <span class="channel-tag">{{ item.channel }}</span>
                    <span class="rac-msg-type">{{ item.message_type }}</span>
                    <span class="rac-msg-by">via {{ item.generated_by }}</span>
                </div>
                <div class="rac-msg-body" id="msg-body-{{ item.id }}">{{ item.body or '' }}</div>
            </div>

            {# ── Actions ─────────────────────────────────────────────── #}
            <div class="rac-actions">
                <button class="action-btn approve-btn" onclick="approveOutreach({{ item.id }})">✅ Aprobar y enviar</button>
                <button class="action-btn" onclick="editOutreach({{ item.id }})">✏️ Editar</button>
                <button class="action-btn reject-btn" onclick="rejectOutreach({{ item.id }})">❌ Descartar</button>
                <button class="action-btn" onclick="showDetail({{ item.lead_id }})" style="margin-left:auto">📋 Ver lead completo</button>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {# ── Filters ──────────────────────────────────────────────────── #}
    <div class="filter-section">
        <div class="filters">
            <span style="font-size:11px;color:var(--text-dim);margin-right:4px">Estado:</span>
            <a href="?{{ pagination_url(1)|replace('page=1&', '')|replace('page=1', '') }}&status=all" class="{{ 'active' if status == 'all' else '' }}">Todos</a>
            <a href="?status=discovered" class="{{ 'active' if status == 'discovered' else '' }}">Descubiertos</a>
            <a href="?status=qualified" class="{{ 'active' if status == 'qualified' else '' }}">Calificados</a>
            <a href="?status=contacted" class="{{ 'active' if status == 'contacted' else '' }}">Contactados</a>
            <a href="?status=in_discussion" class="{{ 'active' if status == 'in_discussion' else '' }}">En Discusión</a>
            <a href="?status=proposal_sent" class="{{ 'active' if status == 'proposal_sent' else '' }}">Propuesta</a>
            <a href="?status=won" class="{{ 'active' if status == 'won' else '' }}">Won</a>
            <a href="?status=lost" class="{{ 'active' if status == 'lost' else '' }}">Lost</a>
        </div>
        <div class="filters" style="margin-top:6px">
            <span style="font-size:11px;color:var(--text-dim);margin-right:4px">Score:</span>
            <input type="number" class="filter-input" id="minScore" placeholder="Min" value="{{ min_score if min_score > 0 else '' }}" style="width:60px"
                   onchange="applyFilters()">
            <span style="color:var(--text-dim)">–</span>
            <input type="number" class="filter-input" id="maxScore" placeholder="Max" value="{{ max_score if max_score < 100 else '' }}" style="width:60px"
                   onchange="applyFilters()">
            <span style="margin-left:8px"></span>
            <select class="filter-select" id="sourceFilter" onchange="applyFilters()">
                <option value="">Todas las fuentes</option>
                {% for src in stats.by_source %}
                <option value="{{ src.fuente }}" {{ 'selected' if signal_source == src.fuente else '' }}>{{ src.fuente }} ({{ src.count }})</option>
                {% endfor %}
            </select>
            <select class="filter-select" id="industryFilter" onchange="applyFilters()">
                <option value="">Todas las industrias</option>
                {% for ind in stats.by_industry %}
                <option value="{{ ind.industry }}" {{ 'selected' if industry == ind.industry else '' }}>{{ ind.industry or '—' }} ({{ ind.count }})</option>
                {% endfor %}
            </select>
            <select class="filter-select" id="locationFilter" onchange="applyFilters()">
                <option value="">Todas las ubicaciones</option>
                {% for loc in locations %}
                <option value="{{ loc }}" {{ 'selected' if location == loc else '' }}>{{ loc[:35] }}</option>
                {% endfor %}
            </select>
            <select class="filter-select" id="campaignFilter" onchange="applyFilters()">
                <option value="0">Todas las campañas</option>
                {% for camp in campaigns %}
                <option value="{{ camp.id }}" {{ 'selected' if campaign_id == camp.id else '' }}>{{ camp.name }} ({{ camp.lead_count }})</option>
                {% endfor %}
            </select>
            <select class="filter-select" id="perPage" onchange="applyFilters()">
                <option value="25" {{ 'selected' if limit == 25 else '' }}>25 / pág</option>
                <option value="50" {{ 'selected' if limit == 50 else '' }}>50 / pág</option>
                <option value="100" {{ 'selected' if limit == 100 else '' }}>100 / pág</option>
            </select>
            {% if campaign %}
            <span class="status-badge status-proposal" style="font-size:12px">📁 {{ campaign.name }}</span>
            <a href="?" class="action-btn reject-btn" style="font-size:10px;padding:2px 8px">✕ Limpiar</a>
            {% endif %}
        </div>
    </div>

    {# ── Campaigns Section ────────────────────────────────────────── #}
    <h2>📁 Campañas <button class="action-btn primary" onclick="showCampaignModal()" style="font-size:12px;margin-left:12px;padding:4px 14px">+ Nueva Campaña</button></h2>
    {% if campaigns %}
    <div class="table-wrapper" style="margin-bottom:20px">
        <table class="lead-table">
            <thead>
                <tr>
                    <th>Nombre</th>
                    <th>Países</th>
                    <th>Industrias</th>
                    <th>Servicios</th>
                    <th>Leads</th>
                    <th>Acciones</th>
                </tr>
            </thead>
            <tbody>
                {% for camp in campaigns %}
                <tr>
                    <td>
                        <a href="?campaign_id={{ camp.id }}">{{ camp.name }}</a>
                        {% if camp.description %}<span style="font-size:10px;color:var(--text-dim);display:block">{{ camp.description[:60] }}</span>{% endif %}
                    </td>
                    <td><span style="font-size:11px;color:var(--text-dim)">{{ camp.target_countries|from_json|join(', ') or '—' }}</span></td>
                    <td><span style="font-size:11px;color:var(--text-dim)">{{ camp.target_industries|from_json|join(', ') or '—' }}</span></td>
                    <td><span style="font-size:11px;color:var(--text-dim)">{{ camp.target_services|from_json|join(', ') or '—' }}</span></td>
                    <td><span class="score-badge score-warm">{{ camp.lead_count }}</span></td>
                    <td>
                        <button class="action-btn" onclick="editCampaign({{ camp.id }})">✏️</button>
                        <button class="action-btn reject-btn" onclick="deleteCampaign({{ camp.id }})">🗑️</button>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:20px">No hay campañas creadas. Creá una para organizar leads por mercado objetivo.</p>
    {% endif %}

    {# ── Lead Table ───────────────────────────────────────────────── #}
    {% if leads %}
    <div class="table-wrapper">
        <table class="lead-table">
            <thead>
                <tr>
                    <th onclick="sortBy('score_total')">Score <span class="sort-arrow {{ 'active' if sort_by == 'score_total' else '' }}">{{ '▼' if sort_by == 'score_total' and sort_order == 'DESC' else '▲' }}</span></th>
                    <th onclick="sortBy('company_name')">Empresa</th>
                    <th>Industria</th>
                    <th>Señal</th>
                    <th>Contacto</th>
                    <th>Status</th>
                    <th onclick="sortBy('discovered_at')">Descubierto</th>
                    <th>Acciones</th>
                </tr>
            </thead>
            <tbody>
                {% for lead in leads %}
                <tr>
                    <td>
                        <span class="score-badge {{ 'score-hot' if lead.score_total >= 80 else 'score-warm' if lead.score_total >= 60 else 'score-cold' if lead.score_total >= 40 else 'score-discard' }}">
                            {{ lead.score_total|int }}
                        </span>
                    </td>
                    <td>
                        <a href="#" onclick="showDetail({{ lead.id }});return false">{{ lead.company_name }}</a>
                        {% if lead.company_size %}
                        <span style="font-size:10px;color:var(--text-dim);display:block">{{ lead.company_size }} emp.</span>
                        {% endif %}
                    </td>
                    <td><span class="source-badge">{{ lead.industry or '—' }}</span></td>
                    <td>
                        <span class="source-badge">{{ lead.signal_source }}</span>
                        <span class="signal-badge signal-{{ lead.signal_strength }}" style="display:block;margin-top:2px">{{ lead.signal_type }}</span>
                    </td>
                    <td>
                        {% if lead.contact_name %}
                        <span style="font-size:12px">{{ lead.contact_name }}</span>
                        <span style="font-size:10px;color:var(--text-dim);display:block">{{ lead.contact_title or '' }}</span>
                        {% else %}
                        <span style="color:var(--text-dim);font-size:11px">—</span>
                        {% endif %}
                    </td>
                    <td><span class="status-badge status-{{ lead.status }}">{{ lead.status }}</span></td>
                    <td><span style="font-size:11px;color:var(--text-dim)">{{ lead.discovered_at[:10] if lead.discovered_at else '—' }}</span></td>
                    <td>
                        <button class="action-btn" onclick="showDetail({{ lead.id }})" title="Ver detalle">📋</button>
                        {% if lead.status in ('discovered', 'qualified') %}
                        <button class="action-btn" onclick="updateStatus({{ lead.id }}, 'contacted')" title="Marcar como contactado">📞</button>
                        {% endif %}
                        {% if lead.status == 'contacted' %}
                        <button class="action-btn" onclick="updateStatus({{ lead.id }}, 'in_discussion')" title="En discusión">💬</button>
                        {% endif %}
                        {% if lead.status == 'in_discussion' %}
                        <button class="action-btn" onclick="updateStatus({{ lead.id }}, 'proposal_sent')" title="Propuesta enviada">📝</button>
                        {% endif %}
                        {% if lead.status == 'proposal_sent' %}
                        <button class="action-btn approve-btn" onclick="updateStatus({{ lead.id }}, 'won')" title="Ganado">🏆</button>
                        <button class="action-btn reject-btn" onclick="updateStatus({{ lead.id }}, 'lost')" title="Perdido">💀</button>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {# ── Pagination ───────────────────────────────────────────────── #}
    {% set total_pages = (total_leads / limit)|round(0, 'ceil')|int %}
    {% if total_pages > 1 %}
    <div class="pagination">
        <a href="{{ pagination_url(1) }}" class="{{ 'disabled' if page <= 1 else '' }}">««</a>
        <a href="{{ pagination_url(page - 1) }}" class="{{ 'disabled' if page <= 1 else '' }}">«</a>

        {% for p in range([1, page - 2]|max, [total_pages, page + 3]|min + 1) %}
        <a href="{{ pagination_url(p) }}" class="{{ 'active' if p == page else '' }}">{{ p }}</a>
        {% endfor %}

        <a href="{{ pagination_url(page + 1) }}" class="{{ 'disabled' if page >= total_pages else '' }}">»</a>
        <a href="{{ pagination_url(total_pages) }}" class="{{ 'disabled' if page >= total_pages else '' }}">»»</a>
        <span class="page-info">Pág {{ page }} de {{ total_pages }}</span>
    </div>
    {% endif %}

    {% else %}
    <div class="empty-state">
        <div class="icon">🔍</div>
        <p>No hay leads todavía.</p>
        <p style="font-size:13px">Ejecuta <code>python3 lead_scraper.py discover</code> para buscar señales de compra.</p>
        <button class="action-btn primary" onclick="runDiscovery()" style="margin-top:16px;font-size:14px;padding:8px 20px">🔎 Discover Leads</button>
    </div>
    {% endif %}

    {# ── Footer ───────────────────────────────────────────────────── #}
    <div class="last-updated">
        ClientScout v0.1 · {{ stats.leads.total_leads }} leads en pipeline ·
        Última actividad: {{ now.strftime('%Y-%m-%d %H:%M') if now else '—' }}
    </div>
</div>

{# ── Toast Container ──────────────────────────────────────────────── #}
<div class="toast-container" id="toastContainer"></div>

{# ── Modal Container (loaded dynamically) ─────────────────────────── #}
<div id="modalContainer"></div>

<script>
// ═══════════════════════════════════════════════════════════════════════
//  CHARTS
// ═══════════════════════════════════════════════════════════════════════

// Score Distribution
const scoreCtx = document.getElementById('scoreChart');
if (scoreCtx) {
    new Chart(scoreCtx, {
        type: 'bar',
        data: {
            labels: ['Hot (80+)', 'Warm (60-79)', 'Cold (40-59)', 'Discard (<40)'],
            datasets: [{
                label: 'Leads',
                data: [{{ stats.leads.hot_leads }}, {{ stats.leads.warm_leads }}, {{ stats.leads.cold_leads }}, {{ stats.leads.total_leads - stats.leads.hot_leads - stats.leads.warm_leads - stats.leads.cold_leads }}],
                backgroundColor: ['rgba(63,185,80,0.5)', 'rgba(210,153,34,0.5)', 'rgba(139,148,158,0.5)', 'rgba(248,81,73,0.5)'],
                borderColor: ['#3fb950', '#d29922', '#8b949e', '#f85149'],
                borderWidth: 1,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
                x: { ticks: { color: '#8b949e' }, grid: { display: false } }
            }
        }
    });
}

// Source Breakdown
const sourceCtx = document.getElementById('sourceChart');
if (sourceCtx) {
    const sourceData = {{ stats.by_source | tojson }};
    new Chart(sourceCtx, {
        type: 'doughnut',
        data: {
            labels: sourceData.map(d => d.fuente),
            datasets: [{
                data: sourceData.map(d => d.count),
                backgroundColor: ['#58a6ff', '#3fb950', '#d29922', '#bc8cff', '#f85149', '#79c0ff'],
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom', labels: { color: '#8b949e', font: { size: 11 } } }
            }
        }
    });
}

// Industry Breakdown
const industryCtx = document.getElementById('industryChart');
if (industryCtx) {
    const industryData = {{ stats.by_industry | tojson }};
    new Chart(industryCtx, {
        type: 'bar',
        data: {
            labels: industryData.map(d => d.industry || '—'),
            datasets: [{
                label: 'Leads',
                data: industryData.map(d => d.count),
                backgroundColor: 'rgba(121,192,255,0.4)',
                borderColor: '#79c0ff',
                borderWidth: 1,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                x: { beginAtZero: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
                y: { ticks: { color: '#8b949e', font: { size: 10 } }, grid: { display: false } }
            }
        }
    });
}

// Channel Breakdown
const channelCtx = document.getElementById('channelChart');
if (channelCtx) {
    const channelData = {{ stats.outreach_by_channel | tojson }};
    if (channelData.length > 0) {
        new Chart(channelCtx, {
            type: 'bar',
            data: {
                labels: channelData.map(d => d.channel),
                datasets: [
                    { label: 'Enviados', data: channelData.map(d => d.sent || 0), backgroundColor: 'rgba(88,166,255,0.5)', borderColor: '#58a6ff', borderWidth: 1 },
                    { label: 'Respondidos', data: channelData.map(d => d.replied || 0), backgroundColor: 'rgba(63,185,80,0.5)', borderColor: '#3fb950', borderWidth: 1 },
                ]
            },
            options: {
                responsive: true,
                plugins: { legend: { labels: { color: '#8b949e', font: { size: 10 } } } },
                scales: {
                    y: { beginAtZero: true, ticks: { color: '#8b949e' }, grid: { color: '#21262d' } },
                    x: { ticks: { color: '#8b949e' }, grid: { display: false } }
                }
            }
        });
    } else {
        channelCtx.parentElement.innerHTML = '<p style="color:var(--text-dim);text-align:center;padding:40px">Sin datos de outreach aún</p>';
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  ACTIONS
// ═══════════════════════════════════════════════════════════════════════

function applyFilters() {
    const params = new URLSearchParams(window.location.search);

    const search = document.getElementById('searchInput').value;
    if (search) params.set('search', search); else params.delete('search');

    const minScore = document.getElementById('minScore').value;
    if (minScore) params.set('min_score', minScore); else params.delete('min_score');

    const maxScore = document.getElementById('maxScore').value;
    if (maxScore) params.set('max_score', maxScore); else params.delete('max_score');

    const source = document.getElementById('sourceFilter').value;
    if (source) params.set('signal_source', source); else params.delete('signal_source');

    const industry = document.getElementById('industryFilter').value;
    if (industry) params.set('industry', industry); else params.delete('industry');

    const location = document.getElementById('locationFilter').value;
    if (location) params.set('location', location); else params.delete('location');

    const campaign = document.getElementById('campaignFilter').value;
    if (campaign && campaign !== '0') params.set('campaign_id', campaign); else params.delete('campaign_id');

    const perPage = document.getElementById('perPage').value;
    params.set('limit', perPage);

    params.set('page', '1');
    window.location.search = params.toString();
}

function sortBy(column) {
    const params = new URLSearchParams(window.location.search);
    const currentSort = params.get('sort_by');
    const currentOrder = params.get('sort_order');

    if (currentSort === column) {
        params.set('sort_order', currentOrder === 'DESC' ? 'ASC' : 'DESC');
    } else {
        params.set('sort_by', column);
        params.set('sort_order', 'DESC');
    }
    window.location.search = params.toString();
}

function updateStatus(leadId, newStatus) {
    fetch('/api/lead/' + leadId + '/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus })
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) {
            toast('Lead → ' + newStatus, 'success');
            setTimeout(() => location.reload(), 500);
        } else {
            toast('Error: ' + (data.error || 'unknown'), 'error');
        }
    })
    .catch(e => toast('Error: ' + e.message, 'error'));
}

function showDetail(leadId) {
    fetch('/api/lead/' + leadId)
        .then(r => r.json())
        .then(lead => {
            const signalData = typeof lead.signal_data === 'string' ? JSON.parse(lead.signal_data || '{}') : (lead.signal_data || {});
            const techStack = typeof lead.tech_stack === 'string' ? JSON.parse(lead.tech_stack || '[]') : (lead.tech_stack || []);

            let signalInfo = '';
            if (signalData.job_titles) {
                signalInfo = '<p style="font-size:12px;color:var(--text-dim)"><strong>Roles:</strong> ' + signalData.job_titles.join(', ') + '</p>';
            } else if (signalData.project_title) {
                signalInfo = '<p style="font-size:12px;color:var(--text-dim)"><strong>Proyecto:</strong> ' + signalData.project_title + '</p>';
                if (signalData.project_description) {
                    signalInfo += '<div class="modal-desc" style="max-height:150px">' + signalData.project_description.substring(0, 500) + '</div>';
                }
                if (signalData.project_budget) {
                    signalInfo += '<p style="font-size:12px;color:var(--green)"><strong>Budget est.:</strong> $' + signalData.project_budget + '</p>';
                }
            }

            const html = `
            <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
                <div class="modal-content">
                    <button class="modal-close" onclick="closeModal()">×</button>
                    <h2>${lead.company_name}</h2>
                    <p style="color:var(--text-dim);font-size:13px">
                        ${lead.industry || 'Sin industria'} · ${lead.company_size || 'Tamaño desconocido'} · ${lead.location || ''}
                    </p>

                    <div class="score-breakdown">
                        <div class="score-item">
                            <div class="score-label">Score Total</div>
                            <div class="score-val" style="color:${lead.score_total >= 80 ? 'var(--green)' : lead.score_total >= 60 ? 'var(--yellow)' : 'var(--text-dim)'}">${Math.round(lead.score_total)}/100</div>
                        </div>
                        <div class="score-item">
                            <div class="score-label">Budget</div>
                            <div class="score-val">${Math.round(lead.score_budget)}/30</div>
                        </div>
                        <div class="score-item">
                            <div class="score-label">Need</div>
                            <div class="score-val">${Math.round(lead.score_need)}/35</div>
                        </div>
                        <div class="score-item">
                            <div class="score-label">Accessibility</div>
                            <div class="score-val">${Math.round(lead.score_accessibility)}/20</div>
                        </div>
                        <div class="score-item">
                            <div class="score-label">Tech Fit</div>
                            <div class="score-val">${Math.round(lead.score_techfit)}/15</div>
                        </div>
                    </div>

                    ${signalInfo}

                    <div class="modal-field">
                        <label>Contacto</label>
                        <input type="text" id="editContactName" value="${lead.contact_name || ''}" placeholder="Nombre del contacto">
                        <input type="text" id="editContactTitle" value="${lead.contact_title || ''}" placeholder="Cargo" style="margin-top:4px">
                        <input type="email" id="editContactEmail" value="${lead.contact_email || ''}" placeholder="Email" style="margin-top:4px">
                        <input type="url" id="editContactLinkedin" value="${lead.contact_linkedin || ''}" placeholder="LinkedIn URL" style="margin-top:4px">
                    </div>

                    <div class="modal-field">
                        <label>Notas</label>
                        <textarea id="editNotes" rows="3">${lead.notes || ''}</textarea>
                    </div>

                    <div class="modal-actions">
                        <button class="action-btn primary" onclick="saveLeadDetail(${lead.id})">💾 Guardar</button>
                        <button class="action-btn" onclick="updateStatus(${lead.id}, 'qualified')">⭐ Calificar</button>
                        <button class="action-btn" onclick="updateStatus(${lead.id}, 'contacted')">📞 Contactado</button>
                        <button class="action-btn" onclick="updateStatus(${lead.id}, 'lost')">💀 Lost</button>
                        ${lead.website ? `<a href="${lead.website}" target="_blank" class="action-btn">🌐 Web</a>` : ''}
                        ${lead.contact_linkedin ? `<a href="${lead.contact_linkedin}" target="_blank" class="action-btn">🔗 LinkedIn</a>` : ''}
                    </div>
                </div>
            </div>`;

            document.getElementById('modalContainer').innerHTML = html;
        })
        .catch(e => toast('Error cargando detalle: ' + e.message, 'error'));
}

function saveLeadDetail(leadId) {
    const data = {
        contact_name: document.getElementById('editContactName').value,
        contact_title: document.getElementById('editContactTitle').value,
        contact_email: document.getElementById('editContactEmail').value,
        contact_linkedin: document.getElementById('editContactLinkedin').value,
        notes: document.getElementById('editNotes').value,
    };
    fetch('/api/lead/' + leadId, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { toast('Guardado', 'success'); closeModal(); setTimeout(() => location.reload(), 500); }
        else toast('Error: ' + (d.error || 'unknown'), 'error');
    });
}

function closeModal() {
    document.getElementById('modalContainer').innerHTML = '';
}

// ═══════════════════════════════════════════════════════════════════════
//  APPROVAL QUEUE ACTIONS
// ═══════════════════════════════════════════════════════════════════════

function approveOutreach(attemptId) {
    if (!confirm('¿Enviar este mensaje ahora?')) return;
    fetch('/api/outreach/' + attemptId + '/approve', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                toast('Mensaje aprobado y enviado', 'success');
                document.getElementById('approval-' + attemptId).style.opacity = '0.4';
                setTimeout(() => location.reload(), 1000);
            } else {
                toast('Error: ' + (d.error || 'unknown'), 'error');
            }
        });
}

function editOutreach(attemptId) {
    const newText = prompt('Editar mensaje:', '');
    if (newText === null) return;  // cancelled
    fetch('/api/outreach/' + attemptId + '/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ edited_body: newText })
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) toast('Mensaje editado y aprobado', 'success');
        else toast('Error: ' + (d.error || 'unknown'), 'error');
        setTimeout(() => location.reload(), 500);
    });
}

function rejectOutreach(attemptId) {
    if (!confirm('¿Descartar este mensaje?')) return;
    fetch('/api/outreach/' + attemptId + '/reject', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) toast('Mensaje descartado', 'success');
            else toast('Error', 'error');
            document.getElementById('approval-' + attemptId).remove();
        });
}

// ═══════════════════════════════════════════════════════════════════════
//  DISCOVERY
// ═══════════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════════
//  CAMPAIGNS
// ═══════════════════════════════════════════════════════════════════════

function showCampaignModal(campaignData) {
    const isEdit = !!campaignData;
    const title = isEdit ? 'Editar Campaña' : 'Nueva Campaña';
    const name = isEdit ? campaignData.name : '';
    const desc = isEdit ? (campaignData.description || '') : '';
    const countries = isEdit ? (typeof campaignData.target_countries === 'string' ? JSON.parse(campaignData.target_countries) : (campaignData.target_countries || [])) : [];
    const industries = isEdit ? (typeof campaignData.target_industries === 'string' ? JSON.parse(campaignData.target_industries) : (campaignData.target_industries || [])) : [];
    const services = isEdit ? (typeof campaignData.target_services === 'string' ? JSON.parse(campaignData.target_services) : (campaignData.target_services || [])) : [];

    const allIndustries = ['fintech','healthtech','logistics','ecommerce','saas','insurance','real estate','manufacturing','legaltech','edtech','engineering'];
    const allServices = ['process_automation','custom_development','ai_integration'];
    const serviceLabels = {process_automation:'Automatizacion de Procesos',custom_development:'Desarrollo de Software',ai_integration:'Integracion AI/LLMs'};

    const indChecks = allIndustries.map(i =>
        `<label style="display:inline-block;margin-right:10px;font-size:12px"><input type="checkbox" value="${i}" ${countries.includes(i)?'checked':''}> ${i}</label>`
    ).join('<br>');
    const svcChecks = allServices.map(s =>
        `<label style="display:inline-block;margin-right:12px;font-size:12px"><input type="checkbox" value="${s}" ${services.includes(s)?'checked':''}> ${serviceLabels[s]}</label>`
    ).join('<br>');

    const html = `
    <div class="modal-overlay" onclick="if(event.target===this)closeModal()">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal()">x</button>
            <h2>${title}</h2>
            <div class="modal-field">
                <label>Nombre</label>
                <input type="text" id="campName" value="${name}" placeholder="Ej: LATAM Fintech Q3 2026" style="width:100%">
            </div>
            <div class="modal-field">
                <label>Descripcion</label>
                <input type="text" id="campDesc" value="${desc}" placeholder="Objetivo de la campana" style="width:100%">
            </div>
            <div class="modal-field">
                <label>Industrias target</label>
                <div id="campIndustries">${indChecks}</div>
            </div>
            <div class="modal-field">
                <label>Servicios a ofrecer</label>
                <div id="campServices">${svcChecks}</div>
            </div>
            <div class="modal-field">
                <label>Paises target (separados por coma)</label>
                <input type="text" id="campCountries" value="${countries.join(', ')}" placeholder="Chile, Argentina, Mexico, United States" style="width:100%">
            </div>
            <div class="modal-actions">
                <button class="action-btn primary" onclick="saveCampaign(${isEdit ? campaignData.id : 'null'})">Guardar</button>
                <button class="action-btn" onclick="closeModal()">Cancelar</button>
            </div>
        </div>
    </div>`;
    document.getElementById('modalContainer').innerHTML = html;
}

function saveCampaign(campaignId) {
    const name = document.getElementById('campName').value.trim();
    if (!name) { toast('El nombre es requerido', 'error'); return; }

    const countries = document.getElementById('campCountries').value.split(',').map(s => s.trim()).filter(Boolean);
    const industries = [...document.querySelectorAll('#campIndustries input:checked')].map(cb => cb.value);
    const services = [...document.querySelectorAll('#campServices input:checked')].map(cb => cb.value);

    const data = { name, description: document.getElementById('campDesc').value.trim(), target_countries: countries, target_industries: industries, target_services: services };

    const url = campaignId ? '/api/campaigns/' + campaignId : '/api/campaigns';
    fetch(url, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) })
        .then(r => r.json())
        .then(d => {
            if (d.ok || d.id) { toast('Campana guardada', 'success'); closeModal(); setTimeout(() => location.reload(), 500); }
            else toast('Error: ' + (d.error || 'unknown'), 'error');
        });
}

function editCampaign(campaignId) {
    fetch('/api/campaigns')
        .then(r => r.json())
        .then(campaigns => {
            const camp = campaigns.find(c => c.id === campaignId);
            if (camp) showCampaignModal(camp);
            else toast('Campana no encontrada', 'error');
        });
}

function deleteCampaign(campaignId) {
    if (!confirm('Desactivar esta campana? Los leads no se eliminaran.')) return;
    fetch('/api/campaigns/' + campaignId + '/delete', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) { toast('Campana eliminada', 'success'); setTimeout(() => location.reload(), 500); }
            else toast('Error', 'error');
        });
}

function assignToCampaign(leadId, campaignId) {
    fetch('/api/lead/' + leadId + '/campaign', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({campaign_id: campaignId})
    })
        .then(r => r.json())
        .then(d => {
            if (d.ok) toast('Lead asignado a campana', 'success');
            else toast('Error', 'error');
        });
}

function runDiscovery() {
    toast('🔍 Ejecutando discovery... esto puede tomar ~1 minuto', 'success');
    fetch('/api/discover', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                toast('Discovery completo: ' + d.count + ' leads encontrados', 'success');
                setTimeout(() => location.reload(), 1500);
            } else {
                toast('Error: ' + (d.error || 'unknown'), 'error');
            }
        })
        .catch(e => toast('Error: ' + e.message, 'error'));
}

function runEnrichment() {
    toast('🔬 Enriqueciendo leads...', 'success');
    fetch('/api/enrich', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                toast('Enrichment completo: ' + d.updated + ' leads actualizados', 'success');
                setTimeout(() => location.reload(), 1500);
            } else {
                toast('Error: ' + (d.error || 'unknown'), 'error');
            }
        })
        .catch(e => toast('Error: ' + e.message, 'error'));
}

function generateOutreach() {
    if (!confirm('¿Generar mensajes de outreach para leads calificados? Se crearan como drafts para que los revises.')) return;
    toast('✉️ Generando mensajes...', 'success');
    fetch('/api/outreach/generate', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                toast(d.drafts + ' mensajes generados. Revisa la Approval Queue.', 'success');
                setTimeout(() => location.reload(), 1500);
            } else {
                toast('Error: ' + (d.error || 'unknown'), 'error');
            }
        })
        .catch(e => toast('Error: ' + e.message, 'error'));
}

// ═══════════════════════════════════════════════════════════════════════
//  UTILS
// ═══════════════════════════════════════════════════════════════════════

function toast(msg, type) {
    const container = document.getElementById('toastContainer');
    const el = document.createElement('div');
    el.className = 'toast ' + (type || '');
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 200); }, 3000);
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
    if (e.key === '/' && document.activeElement === document.body) {
        e.preventDefault();
        document.getElementById('searchInput').focus();
    }
    if (e.key === 'r' && e.ctrlKey) { e.preventDefault(); location.reload(); }
});
</script>
</body>
</html>'''


# ══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Main dashboard page."""
    # Parse query params
    search = request.args.get("search", "")
    status = request.args.get("status", "all")
    min_score = float(request.args.get("min_score", 0) or 0)
    max_score = float(request.args.get("max_score", 100) or 100)
    industry = request.args.get("industry", "")
    signal_source = request.args.get("signal_source", "")
    location = request.args.get("location", "")
    campaign_id = int(request.args.get("campaign_id", 0) or 0)
    sort_by = request.args.get("sort_by", "score_total")
    sort_order = request.args.get("sort_order", "DESC")
    limit = int(request.args.get("limit", 25))
    page = int(request.args.get("page", 1))
    offset = (page - 1) * limit

    # Fetch data
    stats = get_stats(campaign_id=campaign_id)
    leads, total_leads = get_leads(
        search=search, status=status, min_score=min_score, max_score=max_score,
        industry=industry, signal_source=signal_source,
        location=location, campaign_id=campaign_id,
        sort_by=sort_by, sort_order=sort_order,
        limit=limit, offset=offset,
    )
    pending_approvals = get_pending_approvals()
    campaigns = get_campaigns()
    locations = get_distinct_locations()
    campaign = get_campaign(campaign_id) if campaign_id > 0 else None

    return render_template_string(
        HTML_TEMPLATE,
        stats=stats,
        leads=leads,
        total_leads=total_leads,
        pending_approvals=pending_approvals,
        campaigns=campaigns,
        locations=locations,
        campaign=campaign,
        search=search,
        status=status,
        min_score=min_score,
        max_score=max_score,
        industry=industry,
        signal_source=signal_source,
        location=location,
        campaign_id=campaign_id,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        page=page,
        now=datetime.now(),
    )


# ══════════════════════════════════════════════════════════════════════════
#  API ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/stats")
def api_stats():
    """Get pipeline statistics."""
    return jsonify(get_stats())


@app.route("/api/leads")
def api_leads():
    """Get filtered lead list (JSON)."""
    search = request.args.get("search", "")
    status = request.args.get("status", "all")
    min_score = float(request.args.get("min_score", 0) or 0)
    max_score = float(request.args.get("max_score", 100) or 100)
    industry = request.args.get("industry", "")
    signal_source = request.args.get("signal_source", "")
    location = request.args.get("location", "")
    campaign_id = int(request.args.get("campaign_id", 0) or 0)
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    leads, total = get_leads(
        search=search, status=status, min_score=min_score, max_score=max_score,
        industry=industry, signal_source=signal_source,
        location=location, campaign_id=campaign_id,
        limit=limit, offset=offset,
    )
    return jsonify({"leads": leads, "total": total})


@app.route("/api/lead/<int:lead_id>")
def api_lead_detail(lead_id):
    """Get a single lead with full detail."""
    lead = get_lead(lead_id)
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    # Also fetch outreach history
    outreach = get_outreach_for_lead(lead_id)
    lead["outreach_history"] = outreach
    return jsonify(lead)


@app.route("/api/lead/<int:lead_id>", methods=["POST"])
def api_lead_update(lead_id):
    """Update lead contact info and notes."""
    data = request.get_json() or {}
    update_lead_contact(
        lead_id,
        contact_name=data.get("contact_name", ""),
        contact_title=data.get("contact_title", ""),
        contact_email=data.get("contact_email", ""),
        contact_linkedin=data.get("contact_linkedin", ""),
    )
    if data.get("notes"):
        update_lead_status(lead_id, get_lead(lead_id)["status"], notes=data["notes"])
    return jsonify({"ok": True})


@app.route("/api/lead/<int:lead_id>/status", methods=["POST"])
def api_lead_status(lead_id):
    """Update lead pipeline status."""
    data = request.get_json() or {}
    new_status = data.get("status", "")
    notes = data.get("notes", "")

    valid_statuses = ["discovered", "qualified", "contacted", "in_discussion",
                      "proposal_sent", "won", "lost"]
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Valid: {valid_statuses}"}), 400

    update_lead_status(lead_id, new_status, notes)
    return jsonify({"ok": True, "status": new_status})


@app.route("/api/outreach/<int:attempt_id>/approve", methods=["POST"])
def api_outreach_approve(attempt_id):
    """Approve an outreach draft and SEND it via Playwright (LinkedIn) or SMTP (email)."""
    data = request.get_json() or {}
    edited_body = data.get("edited_body", "")

    # First approve in DB
    if edited_body:
        ok = approve_outreach(attempt_id, edited_body)
    else:
        ok = approve_outreach(attempt_id)

    if not ok:
        return jsonify({"error": "Attempt not found or not in draft status"}), 400

    # ── Get the attempt + lead data for sending ─────────────────────
    conn = get_connection()
    attempt = conn.execute("""
        SELECT oa.*, l.contact_linkedin, l.contact_email, l.linkedin_url,
               l.contact_name, l.company_name
        FROM outreach_attempts oa
        JOIN leads l ON oa.lead_id = l.id
        WHERE oa.id = ?
    """, (attempt_id,)).fetchone()
    conn.close()

    if not attempt:
        return jsonify({"error": "Attempt data not found"}), 400

    channel = attempt["channel"]
    body = edited_body or attempt["body"] or ""
    target_url = attempt["contact_linkedin"] or attempt["linkedin_url"] or ""

    # ── Execute actual send ─────────────────────────────────────────
    send_result = {"ok": False, "action": channel, "error": "", "screenshot": ""}

    if channel == "linkedin_connect":
        if not target_url:
            mark_outreach_failed(attempt_id, "No LinkedIn URL available")
            return jsonify({"ok": False, "error": "No LinkedIn URL available for this lead"})

        try:
            from playwright_outreach import LinkedInOutreach
            outreach = LinkedInOutreach(headless=True)
            send_result = outreach.send_connection_request(target_url, body)
        except Exception as e:
            send_result["error"] = f"Playwright error: {e}"

    elif channel == "linkedin_dm":
        if not target_url:
            mark_outreach_failed(attempt_id, "No LinkedIn URL available")
            return jsonify({"ok": False, "error": "No LinkedIn URL available"})

        try:
            from playwright_outreach import LinkedInOutreach
            outreach = LinkedInOutreach(headless=True)
            send_result = outreach.send_dm(target_url, body)
        except Exception as e:
            send_result["error"] = f"Playwright error: {e}"

    elif channel == "email":
        # Email sending not implemented yet — requires SMTP config
        send_result["error"] = "Email sending not yet implemented. Configure SMTP."
        send_result["ok"] = False

    else:
        send_result["error"] = f"Unknown channel: {channel}"

    # ── Update DB based on result ───────────────────────────────────
    if send_result.get("ok"):
        metadata = {
            "sent_via": "playwright" if channel.startswith("linkedin") else "manual",
            "screenshot": send_result.get("screenshot", ""),
            "action": send_result.get("action", channel),
        }
        mark_outreach_sent(attempt_id, metadata)

        # Update lead status to 'contacted'
        update_lead_status(attempt["lead_id"], "contacted",
                           f"Outreach sent via {channel}: {send_result.get('action', '')}")

        return jsonify({"ok": True, "sent": True, "action": send_result.get("action"), "channel": channel})
    else:
        error_msg = send_result.get("error", "Unknown error")
        mark_outreach_failed(attempt_id, error_msg)
        return jsonify({"ok": False, "error": error_msg, "action": send_result.get("action"), "channel": channel}), 500


@app.route("/api/outreach/<int:attempt_id>/reject", methods=["POST"])
def api_outreach_reject(attempt_id):
    """Reject an outreach draft."""
    ok = reject_outreach(attempt_id)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": "Attempt not found or not in draft status"}), 400


@app.route("/api/discover", methods=["POST"])
def api_discover():
    """Trigger lead discovery (runs lead_scraper.py)."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        scraper_path = os.path.join(script_dir, "lead_scraper.py")

        result = subprocess.run(
            [sys.executable, scraper_path, "discover", "--limit", "20", "--no-save"],
            capture_output=True, text=True, timeout=120,
            cwd=script_dir,
        )

        if result.returncode == 0:
            # Now score and save
            from lead_scraper import discover_leads, deduplicate_leads
            from lead_scoring import score_all
            from leads_db import upsert_leads

            leads = discover_leads(sources=["linkedin", "upwork"], limit_per_source=20)
            leads = deduplicate_leads(leads)
            leads = score_all(leads)
            count = upsert_leads(leads)

            return jsonify({"ok": True, "count": count, "output": result.stdout[-500:]})
        else:
            return jsonify({"ok": False, "error": result.stderr[-300:]}), 500

    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Discovery timed out (2 min limit)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    """Run lead enrichment (email finding + tech stack detection)."""
    try:
        from outreach_engine import OutreachEngine
        engine = OutreachEngine()
        updated = engine.run_enrichment(limit=50)
        return jsonify({"ok": True, "updated": updated})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/outreach/generate", methods=["POST"])
def api_outreach_generate():
    """Generate outreach drafts for qualified leads."""
    try:
        from outreach_engine import OutreachEngine
        engine = OutreachEngine()
        drafts = engine.generate_drafts(limit=10)
        return jsonify({"ok": True, "drafts": len(drafts)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Get or update configuration."""
    if request.method == "POST":
        data = request.get_json() or {}
        config = save_config(data)
        return jsonify({"ok": True, "config": config})
    return jsonify(get_config())


# ── Campaign API ─────────────────────────────────────────────────────

@app.route("/api/campaigns")
def api_campaigns():
    """List all campaigns."""
    return jsonify(get_campaigns(active_only=False))


@app.route("/api/campaigns", methods=["POST"])
def api_campaigns_create():
    """Create a new campaign."""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Campaign name is required"}), 400
    cid = create_campaign(
        name=name,
        description=data.get("description", ""),
        target_countries=data.get("target_countries", []),
        target_industries=data.get("target_industries", []),
        target_services=data.get("target_services", []),
    )
    return jsonify({"ok": True, "id": cid})


@app.route("/api/campaigns/<int:campaign_id>", methods=["POST"])
def api_campaigns_update(campaign_id):
    """Update a campaign."""
    data = request.get_json() or {}
    ok = update_campaign(campaign_id, **data)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": "Campaign not found"}), 404


@app.route("/api/campaigns/<int:campaign_id>/delete", methods=["POST"])
def api_campaigns_delete(campaign_id):
    """Soft-delete a campaign."""
    ok = delete_campaign(campaign_id)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"error": "Campaign not found"}), 404


@app.route("/api/lead/<int:lead_id>/campaign", methods=["POST"])
def api_lead_campaign(lead_id):
    """Assign lead to a campaign."""
    data = request.get_json() or {}
    cid = data.get("campaign_id")  # None or int

    if cid is not None:
        cid = int(cid)
    else:
        cid = None

    assign_lead_to_campaign(lead_id, cid)
    return jsonify({"ok": True})


@app.route("/api/locations")
def api_locations():
    """Get distinct locations for filter dropdown."""
    return jsonify(get_distinct_locations())


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ClientScout Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=5004, help="Port (default: 5004)")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    print(f"🔍 ClientScout Dashboard starting on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)
