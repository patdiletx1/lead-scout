# ClientScout — B2B Lead Discovery & Outreach Pipeline

Sistema automatizado de prospección B2B para servicios de automatización de procesos y desarrollo de software. Descubre señales de compra en múltiples fuentes, califica leads, y gestiona outreach multi-canal con aprobación manual.

## Arquitectura

```
🔍 SIGNAL DISCOVERY          📊 LEAD SCORING          ✉️ OUTREACH
┌─────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ lead_scraper.py │ →  │ lead_scoring.py  │ →  │ outreach_engine  │
│                 │    │                  │    │ (Phase 2)        │
│ • LinkedIn Jobs │    │ Budget     (30)  │    │                  │
│ • Upwork RSS    │    │ Need       (35)  │    │ LinkedIn Connect │
│ • Crunchbase    │    │ Access     (20)  │    │ LinkedIn DM      │
│ • Clutch.co     │    │ Tech Fit   (15)  │    │ Cold Email       │
│ • Reddit/HN     │    │ ─────────────    │    │ Upwork Proposal  │
│                 │    │ Total    (100)   │    │                  │
└────────┬────────┘    └────────┬─────────┘    └────────┬─────────┘
         │                      │                        │
         └──────────────────────┼────────────────────────┘
                                ▼
                    ┌──────────────────────┐
                    │     leads.db         │
                    │  (SQLite WAL mode)   │
                    │                      │
                    │  • leads             │
                    │  • outreach_attempts │
                    │  • deals             │
                    │  • activity_log      │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │  lead_dashboard.py   │
                    │  (Flask + Chart.js)  │
                    │                      │
                    │  • Pipeline Funnel   │
                    │  • Approval Queue    │
                    │  • Stats & Charts    │
                    │  • Lead Table        │
                    └──────────────────────┘
```

## Archivos

| Archivo | Líneas | Descripción |
|---------|--------|-------------|
| `leads_db.py` | ~430 | SQLite schema, CRUD, stats, config |
| `scout_profile.py` | ~140 | Perfil de consultor, servicios, casos de estudio |
| `lead_scoring.py` | ~290 | Scoring engine: Budget + Need + Access + TechFit |
| `lead_scraper.py` | ~470 | Signal discovery: LinkedIn Jobs + Upwork RSS |
| `lead_dashboard.py` | ~600 | Flask dashboard con Approval Queue |
| `deploy/` | — | systemd services, cron scripts, Caddy config |

## Quick Start

```bash
# 1. Discover leads from all sources
python3 lead_scraper.py discover

# 2. Start dashboard
python3 lead_dashboard.py --port 5004

# 3. Open http://localhost:5004
```

## API Endpoints

| Endpoint | Method | Descripción |
|----------|--------|-------------|
| `/api/stats` | GET | Pipeline statistics |
| `/api/leads` | GET | Filtered lead list |
| `/api/lead/<id>` | GET/POST | Lead detail + update |
| `/api/lead/<id>/status` | POST | Update pipeline status |
| `/api/outreach/<id>/approve` | POST | Approve and send outreach |
| `/api/outreach/<id>/reject` | POST | Reject outreach draft |
| `/api/discover` | POST | Run discovery cycle |
| `/api/config` | GET/POST | View/update configuration |

## Deploy (VPS)

```bash
# From local machine:
./deploy/deploy.sh

# Or manually:
scp *.py root@91.99.157.147:/opt/data/home/lead_scout/
ssh root@91.99.157.147 "
    cp /opt/data/home/lead_scout/deploy/leads-dashboard.service /etc/systemd/system/
    systemctl daemon-reload && systemctl restart leads-dashboard
"
```

## Scoring Formula

```
LEAD_SCORE (0-100) =
    Budget Signal  × 30   Funding, company size, salary ranges
  + Need Signal   × 35   Active hiring, tech debt, transformation
  + Accessibility × 20   Decision-maker reachable (LinkedIn + email)
  + Tech Fit      × 15   Industry match, tech stack overlap
```

| Score | Classification | Action |
|-------|---------------|--------|
| 80-100 | Hot | Priority outreach |
| 60-79 | Warm | Outreach with personalization |
| 40-59 | Cold | Nurture, auto-generate generic |
| <40 | Discard | Skip or archive |

## Pipeline Stages

```
discovered → qualified → contacted → in_discussion → proposal_sent → won
                                                                    → lost
```

## Fases

- [x] **Fase 1: Foundation** — DB + Discovery + Scoring + Dashboard
- [ ] **Fase 2: Outreach** — LinkedIn automation + Approval Queue
- [ ] **Fase 3: Enrichment + Email** — Hunter.io + cold email
- [ ] **Fase 4: Optimization** — Deals, ROI tracking, A/B testing
