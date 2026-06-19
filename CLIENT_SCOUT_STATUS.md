# ClientScout — Estado del Proyecto

> Última actualización: 2026-06-19  
> Repo: patdiletx1/lead-scout (próximamente)  
> Dashboard: https://leads.patdilet.dev  
> VPS: Hetzner CPX32 (91.99.157.147) — Container hermes  

---

## Resumen Ejecutivo

ClientScout es un sistema de prospección B2B que descubre empresas con necesidades de automatización de procesos o desarrollo de software, las califica, genera mensajes personalizados con IA, y ejecuta outreach multi-canal (LinkedIn + email). Es la evolución del Auto-Apply Twin (búsqueda de empleo) aplicada a venta de servicios.

### Métricas actuales

| Métrica | Valor |
|---------|-------|
| Leads descubiertos/día | 150-180 |
| Fuentes activas | LinkedIn Jobs (señales de compra) |
| Precisión de scoring | 4 dimensiones (Budget, Need, Access, TechFit) |
| Mensajes generados | DeepSeek vía API (personalizados por lead) |
| Canales de outreach | LinkedIn connect request (enviado real) |
| Campañas | 1 creada (LATAM Fintech Q3 2026) |

---

## Arquitectura

```
                        CLIENT SCOUT ARCHITECTURE
                              v1.0.0

┌─────────────────────────────────────────────────────────────────────┐
│                    SIGNAL DISCOVERY LAYER                             │
│                                                                      │
│  lead_scraper.py (698 líneas)                                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ LinkedIn Jobs Guest API                                       │  │
│  │ 5 keywords × 5 locations = 25 búsquedas                       │  │
│  │ Señales: hiring_cto, transformation, legacy_modernization,    │  │
│  │          hiring_spree, automation_need                         │  │
│  │ 150-180 leads/día                                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              ↓                                       │
│  lead_scoring.py (444 líneas)                                       │
│  Budget(30) + Need(35) + Accessibility(20) + TechFit(15) = 0-100   │
│                              ↓                                       │
│  leads_db.py → SQLite WAL (leads, outreach_attempts, deals,        │
│                campaigns, activity_log, scout_config)               │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    ENRICHMENT LAYER                                   │
│                                                                      │
│  lead_enricher.py (330 líneas)                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ • Email finding: Hunter.io API + pattern guessing             │  │
│  │ • Tech stack detection: 25 tecnologías desde job descriptions │  │
│  │ • Company info: industry, size inference                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              ↓                                       │
│  outreach_engine.py (340 líneas) — Orquestador                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ enrich → re-score → generate → draft → approve → send        │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    OUTREACH LAYER                                     │
│                                                                      │
│  llm_outreach_writer.py (380 líneas)                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ DeepSeek API (deepseek-chat)                                   │  │
│  │ System prompt: perfil + servicios + 4 casos de estudio         │  │
│  │ Tipos: linkedin_connect, linkedin_dm, cold_email,             │  │
│  │        follow_up, breakup, proposal_outline                    │  │
│  │ Secuencia: 1→4 mensajes según datos disponibles               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  playwright_outreach.py (610 líneas)                                 │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Chromium headless (Playwright)                                 │  │
│  │ Sesión persistente (.playwright_sessions/linkedin/)           │  │
│  │ Acciones: connect_request, direct_message                     │  │
│  │ Human-like delays + anti-detection                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ⚠️ email_sender.py — NO IMPLEMENTADO                               │
│  ⚠️ LinkedIn 2FA — PENDIENTE en sesión actual                       │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    DASHBOARD (Flask + Chart.js)                       │
│                                                                      │
│  lead_dashboard.py (1,400+ líneas) — Single-file deployment          │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ • Stats cards: pipeline completo                              │  │
│  │ • Approval Queue: revisar, editar, aprobar/rechazar mensajes  │  │
│  │ • Campañas: CRUD, filtro, stats por campaña                   │  │
│  │ • Charts: score distribution, fuentes, industrias, canales    │  │
│  │ • Lead table: filtros, paginación, modal de detalle           │  │
│  │ • API: 15 endpoints REST                                       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  Dominio: https://leads.patdilet.dev (Caddy + Basic Auth)           │
│  Puerto: 5004 (systemd: leads-dashboard.service)                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Componentes — Detalle

### 1. leads_db.py (781 líneas)
**Capa de datos.** SQLite con WAL mode, row_factory, foreign keys.

**Tablas:**
- `leads` — 29 columnas incluyendo scoring, contacto, tech_stack, campaign_id
- `outreach_attempts` — 16 columnas, tracking completo de cada mensaje
- `deals` — 12 columnas, pipeline de ventas con expected value
- `campaigns` — 8 columnas, agrupación por mercado objetivo
- `activity_log` — registro de acciones
- `scout_config` — configuración persistente (key-value JSON)

**Funciones clave (25+):**
- CRUD: `upsert_leads`, `get_lead`, `update_lead_status`, `update_lead_contact`
- Outreach: `create_outreach_attempt`, `approve_outreach`, `reject_outreach`, `mark_outreach_sent`, `mark_outreach_failed`
- Campaigns: `create_campaign`, `update_campaign`, `delete_campaign`, `get_campaigns`, `assign_lead_to_campaign`
- Queries: `get_leads` (13 filtros), `get_stats` (con campaign_id), `get_pending_approvals`, `get_leads_for_outreach`, `get_leads_needing_followup`
- Config: `get_config`, `save_config`

### 2. scout_profile.py (151 líneas)
**Perfil del consultor.** Datos de Patricio, servicios ofrecidos, casos de estudio, keywords de señales de compra, industrias target, anti-targets.

### 3. lead_scoring.py (444 líneas)
**Motor de calificación.** Evalúa cada lead en 4 dimensiones:

| Dimensión | Máx | Criterio |
|-----------|-----|----------|
| Budget Signal | 30 | Funding, company size, salary ranges en job posts |
| Need Signal | 35 | Tipo y fuerza de la señal (CTO hiring = 35, automation need = 18) |
| Accessibility | 20 | Contacto en LinkedIn, email verificado, decision-maker |
| Tech Fit | 15 | Tech stack overlap, industria target, servicios aplicables |

Clasificación: Hot (80+), Warm (60-79), Cold (40-59), Discard (<40)

### 4. lead_scraper.py (698 líneas)
**Descubrimiento de señales.** Busca en LinkedIn Jobs usando la API pública (guest):

- 5 categorías de keywords: hiring_cto, transformation, legacy_modernization, hiring_spree, automation_need
- 5 locaciones: Chile, United States, Remote, Latin America, Spain
- Normalización a lead dicts estandarizados
- Deduplicación cross-source por company_name
- Flag `--campaign-id` para scoping geográfico y auto-asignación

**Upwork RSS** — Implementado pero feeds retornan 410 (URLs expiradas).

### 5. lead_enricher.py (330 líneas)
**Enriquecimiento de leads.** Capa sin equivalente en el Auto-Apply Twin:

- **Email finding**: Hunter.io API (si configurado) + pattern guessing (first.last@domain, etc.)
- **Tech stack detection**: 25 tecnologías desde texto de job descriptions
- **Company info**: Inferencia de industria y tamaño

### 6. llm_outreach_writer.py (380 líneas)
**Generación de mensajes con IA.** Usa DeepSeek (`deepseek-chat`) vía API:

- **System prompt**: perfil completo + servicios + 4 casos de estudio + reglas de estilo
- **6 tipos de mensaje**: linkedin_connect, linkedin_dm, cold_email, follow_up, breakup, proposal_outline
- **Detección de idioma**: español para LATAM/España, inglés para US/global
- **Contexto automático**: interpreta la señal para personalizar el mensaje
- **Fallback a templates**: si no hay API key disponible
- **Secuencia completa**: `generate_sequence()` produce 1-4 mensajes según datos del lead

### 7. outreach_engine.py (340 líneas)
**Orquestador del pipeline de outreach:**
- `preview()` — Lista candidatos sin generar
- `generate_drafts()` — Enrich → re-score → generate → create draft
- `run_enrichment()` — Enrich + re-score batch de leads
- `stats()` — Pipeline stats

### 8. playwright_outreach.py (610 líneas)
**Automatización de LinkedIn.** Envía connection requests y DMs reales:

- Usa la sesión persistente de LinkedIn (`.playwright_sessions/linkedin/`)
- Chromium headless con anti-detection flags
- Human-like delays, scrolls aleatorios, movimientos de mouse
- `send_connection_request(linkedin_url, message)` — Click Connect → Add Note → pegar mensaje → Send
- `send_dm(linkedin_url, message)` — Click Message → pegar texto → Send
- `check_connection_status(linkedin_url)` — Detecta connected/pending/not_connected

**⚠️ Bloqueado por 2FA:** La sesión de LinkedIn actual tiene una pantalla de 2FA pendiente.

### 9. email_sender.py (216 líneas) — NUEVO Fase 5
**Envío de cold emails.** Soporta dos métodos:

- **Gmail SMTP** — Usa App Password (no la contraseña regular). Requiere 2FA activado en la cuenta Google.
- **SendGrid API** — Si `SENDGRID_API_KEY` está configurada, toma prioridad.

Métodos: `send(to_email, subject, body, to_name, language)` → dict con ok/message_id/error
Configuración: `GMAIL_APP_EMAIL`, `GMAIL_APP_PASSWORD`, `SENDGRID_API_KEY`

### 10. save_session.py (161 líneas) — NUEVO Fase 5
**Login manual de LinkedIn en Mac local.** Abre Chromium con Playwright, el usuario hace login manual (incluyendo 2FA), y guarda la sesión para subir al VPS.

### 11. lead_dashboard.py (1,400+ líneas)
**Dashboard web.** Flask single-file con HTML/CSS/JS inline (mismo patrón que jobs.patdilet.dev):

- **Stats cards**: 10 métricas del pipeline
- **Pipeline bar**: funnel visual con 7 etapas
- **Charts (Chart.js)**: score distribution, fuentes, industrias, canales
- **Approval Queue**: tarjetas ricas con contexto (por qué, qué ofrecer, score, mensaje)
- **Campañas**: tabla + modal CRUD + filtro
- **Lead table**: 13 columnas con filtros, paginación, ordenamiento
- **Modal de detalle**: score breakdown, signal data, contacto, acciones
- **API REST**: 15 endpoints (stats, leads, lead detail, outreach approve/reject, campaigns CRUD, discover, enrich, generate)

---

## Infraestructura

### VPS (Hetzner CPX32)
| Componente | Detalle |
|-----------|---------|
| IP | 91.99.157.147 |
| Container | hermes (Docker, host networking) |
| Python | 3.13.5 (venv: /opt/hermes/.venv) |
| Playwright | 1.60.0 + Chromium headless |
| LLM | DeepSeek (`deepseek-chat`) vía API |
| DB | SQLite WAL (`/opt/data/home/lead_scout/leads.db`) |
| Dashboard | Flask :5004, systemd `leads-dashboard.service` |

### Servicios systemd
| Servicio | Schedule | Descripción |
|----------|----------|-------------|
| `leads-dashboard.service` | Always on | Dashboard Flask en :5004 |
| `leads-discovery.timer` | Lun-Vie 13:00 UTC (9 AM CLT) | Descubrimiento diario |

### Proxy
| Atributo | Valor |
|----------|-------|
| Proxy | Caddy (bare-metal) |
| Dominio | https://leads.patdilet.dev |
| SSL | Let's Encrypt automático |
| Auth | Basic Auth (`patdilet` / `patdilet2026`) |
| Backend | localhost:5004 |

---

## API Endpoints

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/stats` | GET | Pipeline statistics |
| `/api/leads` | GET | Filtered lead list (13 params) |
| `/api/lead/<id>` | GET | Lead detail + outreach history |
| `/api/lead/<id>` | POST | Update contact info + notes |
| `/api/lead/<id>/status` | POST | Update pipeline status |
| `/api/lead/<id>/campaign` | POST | Assign lead to campaign |
| `/api/outreach/<id>/approve` | POST | Approve + send (Playwright/email) |
| `/api/outreach/<id>/reject` | POST | Reject draft |
| `/api/outreach/generate` | POST | Generate outreach drafts |
| `/api/discover` | POST | Run lead discovery |
| `/api/enrich` | POST | Run lead enrichment |
| `/api/campaigns` | GET | List campaigns |
| `/api/campaigns` | POST | Create campaign |
| `/api/campaigns/<id>` | POST | Update campaign |
| `/api/campaigns/<id>/delete` | POST | Delete campaign |
| `/api/locations` | GET | Distinct locations list |
| `/api/config` | GET/POST | View/update configuration |

---

## Flujo Completo

```
⏰ Cron (L-V 9AM) o botón "Discover"
│
├─► 1. DESCUBRIR
│   lead_scraper.py → LinkedIn Guest API → 25 búsquedas
│   → 150-180 leads crudos → lead_scoring.py → upsert_leads()
│   Status: discovered
│
├─► 2. ENRIQUECER (botón "Enrich")
│   outreach_engine.py enrich → LeadEnricher
│   → Tech stack detection → re-score → update DB
│
├─► 3. GENERAR (botón "Generate")
│   outreach_engine.py generate → LLMOutreachWriter.generate_sequence()
│   → POST DeepSeek → 1-4 mensajes personalizados por lead
│   → create_outreach_attempt() → status: draft
│
├─► 4. REVISAR (Dashboard Approval Queue)
│   Cada tarjeta muestra:
│   • 🔍 Por qué este lead
│   • 🎯 Qué ofrecerle (3 servicios)
│   • 📊 Score breakdown visual
│   • ✉️ Mensaje completo
│   → [✏️ Editar] → modificar texto
│   → [✅ Aprobar] → PASO 5
│   → [❌ Descartar]
│
└─► 5. ENVIAR
    ├─► LinkedIn: playwright_outreach.py → Chromium → Connect/DM → screenshot
    └─► Email: ⚠️ NO IMPLEMENTADO
```

---

## Plan de Desarrollo — Backlog

### 🔴 Fase 5: Desbloquear Envío Real (EN PROGRESO)

| # | Tarea | Archivos | Estado |
|---|-------|----------|--------|
| 5.1 | **Resolver LinkedIn 2FA** — La sesión tiene una pantalla de 2FA pendiente. Usar `save_session.py` en Mac local para login manual + 2FA, luego SCP al VPS. | `save_session.py` | ⚠️ Pendiente acción usuario |
| 5.2 | **Email sender** — Envío de cold emails vía Gmail SMTP (App Password) o SendGrid API. | `email_sender.py` (216 líneas) | ✅ Completado |
| 5.3 | **Git-based deploy** — VPS clona el repo. Deploy es `git pull` en vez de SCP. | `deploy/deploy.sh` | ✅ Completado |
| 5.4 | **Probar envío real end-to-end** — Aprobar un draft → verificar envío en LinkedIn/email. | — | ⏳ Bloqueado por 5.1 |

### 🟡 Fase 6: Mejorar Calidad de Leads

| # | Tarea | Archivos | Esfuerzo |
|---|-------|----------|----------|
| 6.1 | **Hunter.io API key** — Configurar key en hermes, probar email finding en lote de 50 leads. | `lead_enricher.py` (ya implementado) | 1 h |
| 6.2 | **Fix Upwork RSS** — Generar nuevas URLs de feed desde Upwork o implementar scraping directo de project listings. | `lead_scraper.py` | 2-3 h |
| 6.3 | **Crunchbase funding signals** — Usar Crunchbase API (free tier) para detectar empresas con funding reciente. Nueva fuente de discovery. | `lead_scraper.py` | 3-4 h |

### 🟢 Fase 7: Completar el Ciclo

| # | Tarea | Archivos | Esfuerzo |
|---|-------|----------|----------|
| 7.1 | **Follow-up automático** — Cron (Vie 14:00) que detecta leads sin respuesta en 7 días, genera drafts de follow-up vía LLM. `get_leads_needing_followup()` ya existe. | `followup_engine.py` (nuevo) | 2-3 h |
| 7.2 | **Deals UI** — Sección en dashboard para crear deals, asignar valor estimado, tracking de pipeline de ventas con expected value. | `lead_dashboard.py`, `leads_db.py` | 3-4 h |
| 7.3 | **Response monitor** — Adaptar `email_monitor.py` del job search system para escanear Gmail y detectar respuestas de leads. | `response_monitor.py` (nuevo) | 3-4 h |
| 7.4 | **Telegram alerts** — Notificaciones vía Telegram bot cuando: lead hot (80+), nueva respuesta, drafts pendientes de revisión. | `telegram_alerts.py` (nuevo) | 2 h |

### 🔵 Fase 8: Optimización

| # | Tarea | Archivos | Esfuerzo |
|---|-------|----------|----------|
| 8.1 | **A/B testing de mensajes** — Generar 2 variantes por lead, trackear response rate por variante. | `llm_outreach_writer.py`, `leads_db.py` | 4-5 h |
| 8.2 | **Feedback loop de scoring** — Ajustar pesos del scoring engine basado en tasas de respuesta reales por tipo de señal. | `lead_scoring.py` | 2-3 h |
| 8.3 | **Dashboard analytics** — Nuevos charts: response rate por campaña, por tipo de señal, por industria. | `lead_dashboard.py` | 2 h |
| 8.4 | **LinkedIn DM post-connection** — Detectar conexiones aceptadas → enviar DM de seguimiento automáticamente. | `playwright_outreach.py` | 3 h |

---

## Deuda Técnica

| # | Item | Impacto |
|---|------|---------|
| 1 | Sin tests automatizados | Riesgo de regresiones |
| 2 | HTML/CSS/JS inline en dashboard.py | Hard de mantener, considerar templates separados |
| 3 | ~~Sin CI/CD — deploy manual vía SCP~~ → Ahora es `git pull` | ✅ Resuelto |
| 4 | API keys en systemd service (visible en `systemctl cat`) | Mejor usar archivo `.env` |
| 5 | Sin rate limiting en API endpoints | Posible abuso si el basic auth se comparte |
| 6 | Logs solo en journald — sin agregación | Difícil debuggear problemas históricos |

---

## Comandos Útiles

```bash
# Discovery manual
docker exec -e DEEPSEEK_API_KEY=sk-... hermes \
  /opt/hermes/.venv/bin/python3 \
  /opt/data/home/lead_scout/lead_scraper.py discover

# Discovery scoped por campaña
docker exec hermes ... lead_scraper.py discover --campaign-id 1

# Generar drafts
docker exec -e DEEPSEEK_API_KEY=sk-... hermes \
  /opt/hermes/.venv/bin/python3 \
  /opt/data/home/lead_scout/outreach_engine.py generate --max 10

# Enriquecer leads
docker exec hermes ... outreach_engine.py enrich --limit 50

# Pipeline stats
docker exec hermes ... outreach_engine.py stats

# Ver drafts pendientes
curl -s -u patdilet:patdilet2026 https://leads.patdilet.dev/api/stats | python3 -m json.tool

# Dashboard status
systemctl status leads-dashboard.service
journalctl -u leads-dashboard.service --no-pager -n 20

# Deploy (git pull)
cd /Volumes/M2\ SSD/Repos/PDL/patdilet/lead-scout
git push origin main
ssh root@91.99.157.147 "cd /root/.hermes/home/lead_scout && git pull origin main && fuser -k 5004/tcp; systemctl restart leads-dashboard"

# O usar el script:
bash deploy/deploy.sh
```
