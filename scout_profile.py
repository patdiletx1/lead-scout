"""
ClientScout Profile — Patricio Diaz as a B2B automation consultant.

This is the lead-scoring equivalent of scoring_profile.py in the job search system.
Defines the consultant profile, services offered, case studies, and scoring parameters.
"""

PROFILE = {
    "name": "Patricio Diaz",
    "title": "Senior Full Stack Developer & Process Automation Consultant",
    "tagline": "Ayudo empresas a automatizar procesos críticos y construir software a medida.",
    "experience_years": 10,
    "languages": ["es", "en"],
    "timezone": "America/Santiago",
    "website": "https://patdilet.dev",

    # ── Services Offered ────────────────────────────────────────────────
    "services": [
        {
            "id": "process_automation",
            "name": "Automatización de Procesos",
            "name_en": "Process Automation",
            "description": "Automatización end-to-end de flujos de trabajo manuales, integración de sistemas, RPA con Python, y web scraping a escala.",
            "typical_budget_usd": [5000, 25000],
            "typical_duration_months": [1, 4],
            "keywords": ["automation", "workflow", "RPA", "scraping", "integration", "API",
                         "business process", "process optimization", "efficiency"],
        },
        {
            "id": "custom_development",
            "name": "Desarrollo de Software a Medida",
            "name_en": "Custom Software Development",
            "description": "Aplicaciones web, APIs, y sistemas backend con .NET/C#, Python, Azure. Migraciones legacy a moderno.",
            "typical_budget_usd": [10000, 50000],
            "typical_duration_months": [2, 6],
            "keywords": [".NET", "C#", "Azure", "web application", "API development",
                         "backend", "cloud migration", "legacy modernization", "microservices"],
        },
        {
            "id": "ai_integration",
            "name": "Integración de AI / LLMs",
            "name_en": "AI / LLM Integration",
            "description": "Chatbots inteligentes, procesamiento de documentos con LLMs, agentes autónomos, y automatización cognitiva.",
            "typical_budget_usd": [8000, 30000],
            "typical_duration_months": [1, 3],
            "keywords": ["AI", "LLM", "chatbot", "GPT", "OpenAI", "DeepSeek", "agent",
                         "document processing", "RAG", "intelligent automation", "cognitive"],
        },
    ],

    # ── Case Studies ─────────────────────────────────────────────────────
    "case_studies": [
        {
            "id": "omnireport",
            "title": "OmniReport — SaaS de Reportes Técnicos Automatizados",
            "industry": "engineering",
            "problem": "Empresas de ingeniería gastaban 40+ horas/semana generando reportes técnicos manualmente.",
            "solution": "Plataforma multi-tenant SaaS que automatiza la generación de reportes desde múltiples fuentes de datos con IA.",
            "stack": ["Next.js", "Express", "PostgreSQL", "Redis", "BullMQ", "OpenAI", "Gemini"],
            "result": "Reducción del 95% en tiempo de generación de reportes. Procesa 1000+ reportes/día.",
        },
        {
            "id": "autoapply_twin",
            "title": "Auto-Apply Twin — Agente Autónomo de Postulación Laboral",
            "industry": "hrtech",
            "problem": "Postular a ofertas de trabajo manualmente tomaba 30+ minutos por aplicación, limitando a 3-5 apps/día.",
            "solution": "Agente autónomo con Playwright + LLMs que descubre, califica, y postula a ofertas automáticamente.",
            "stack": ["Python", "Playwright", "DeepSeek", "SQLite", "Flask", "systemd"],
            "result": "De 5 a 15 postulaciones/día. 330 apps/mes vs 100 manuales. Tasa de respuesta 3-5%.",
        },
        {
            "id": "autoiva",
            "title": "AutoIVA — Contador Agente para Impuestos Chilenos",
            "industry": "fintech",
            "problem": "Contadores pasaban días completos revisando facturas y cuadrando IVA manualmente contra el SII.",
            "solution": "Agente autónomo que se conecta al SII, procesa facturas electrónicas, y genera declaraciones de IVA automáticamente.",
            "stack": ["Python", "Selenium", "DeepSeek", "PostgreSQL", "Qdrant", "FastAPI"],
            "result": "Automatización completa del ciclo de IVA. Reducción de errores humanos a cero.",
        },
        {
            "id": "deliverycheck",
            "title": "DeliveryCheck AI — Verificador de Entregas con IA",
            "industry": "logistics",
            "problem": "Empresas de delivery perdían dinero por órdenes incorrectas sin verificación eficiente.",
            "solution": "PWA con visión artificial (Gemini) que verifica automáticamente órdenes de delivery contra fotos y recibos.",
            "stack": ["Next.js", "FastAPI", "Gemini 2.5 Flash", "PostgreSQL", "Redis", "Gmail API"],
            "result": "Verificación en segundos vs minutos manuales. 98% precisión en detección de errores.",
        },
    ],

    # ── Target Client Profile ────────────────────────────────────────────
    "target_client": {
        "company_size": ["11-50", "51-200"],      # Startups + SMBs
        "industries": [
            "fintech", "healthtech", "logistics", "ecommerce",
            "saas", "insurance", "real estate", "manufacturing",
            "legaltech", "edtech", "proptech", "engineering",
        ],
        "roles_to_contact": [
            "CTO", "VP of Engineering", "Head of Product",
            "CEO", "Founder", "COO", "Director of Technology",
            "Head of Digital Transformation", "CIO",
        ],
        "regions": ["Chile", "Latin America", "United States", "Canada", "Spain"],
    },

    # ── Signal Keywords ──────────────────────────────────────────────────
    "buy_signals": {
        "urgent_need": [
            "CTO needed", "VP Engineering needed", "Head of Engineering",
            "founding engineer", "first engineering hire",
            "technical co-founder", "looking for CTO",
        ],
        "strong_need": [
            "digital transformation", "process automation", "automation engineer",
            "senior developer", "lead developer", "technical lead",
            "legacy system", "modernization", "migrating from",
            "looking for developers", "hiring developers",
            "scaling engineering team", "growing engineering",
        ],
        "moderate_need": [
            "RPA", "workflow automation", "business process",
            "system integration", "API integration",
            "web scraping", "data extraction",
            "developer needed", "full stack developer",
        ],
        "budget_signals": [
            "series A", "series B", "series C", "recently funded",
            "raised $", "secured $", "announced funding",
            "venture capital", "VC-backed",
        ],
    },

    # ── Anti-Targets (companies/industries to skip) ─────────────────────
    "exclude_industries": [
        "gambling", "casino", "crypto casino", "adult entertainment",
        "onlyfans", "tobacco", "weapons", "pyramid scheme", "MLM",
    ],
    "exclude_keywords": [
        "gambling", "casino", "crypto casino", "adult", "onlyfans",
        "pyramid", "mlm", "multi-level marketing",
    ],

    # ── Scoring Weights ──────────────────────────────────────────────────
    "scoring_weights": {
        "budget_signal": 30,
        "need_signal": 35,
        "accessibility": 20,
        "tech_fit": 15,
    },
}
