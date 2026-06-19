#!/bin/bash
# ClientScout — Daily Lead Discovery Script
# Schedule: Mon-Fri 13:00 UTC (9 AM CLT)
# Runs inside the hermes container via docker exec

set -e

SCRIPT_DIR="/opt/data/home/lead_scout"
cd "$SCRIPT_DIR" || exit 1

echo "=== ClientScout Discovery — $(date -u) ==="

# 1. Discover new leads from all sources
/opt/hermes/.venv/bin/python3 lead_scraper.py discover --limit 25

# 2. Show stats summary
/opt/hermes/.venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from leads_db import get_stats
stats = get_stats()
ls = stats['leads']
print(f'Pipeline: {ls[\"total_leads\"]} total | {ls[\"hot_leads\"]} hot | {ls[\"warm_leads\"]} warm | {ls[\"qualified\"]} qualified')
print(f'Pending approval: {stats[\"outreach\"][\"pending_approval\"]}')
"

echo "=== Done — $(date -u) ==="
