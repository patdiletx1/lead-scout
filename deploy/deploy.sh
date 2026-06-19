#!/bin/bash
# ClientScout — Deploy to VPS via git pull
# Usage: ./deploy/deploy.sh

set -e

VPS_HOST="root@91.99.157.147"
VPS_HOST_DIR="/root/.hermes/home/lead_scout"
VPS_CONTAINER_DIR="/opt/data/home/lead_scout"
CONTAINER="hermes"
VENV_PYTHON="/opt/hermes/.venv/bin/python3"

echo "🔄 Deploying ClientScout to VPS via git pull..."

# 1. Push local changes (if any)
if git diff --quiet && git diff --cached --quiet; then
    echo "📦 No local changes to push"
else
    echo "📦 Pushing local changes..."
    git push origin main
fi

# 2. Pull on VPS
echo "📥 Pulling on VPS..."
ssh "$VPS_HOST" "cd $VPS_HOST_DIR && git pull origin main"

# 3. Validate syntax
echo "🔍 Validating syntax..."
ssh "$VPS_HOST" "docker exec $CONTAINER $VENV_PYTHON -c '
import sys; sys.path.insert(0, \"$VPS_CONTAINER_DIR\")
import py_compile
for mod in [\"leads_db\", \"lead_scoring\", \"lead_scraper\", \"outreach_engine\", \"llm_outreach_writer\", \"playwright_outreach\", \"lead_enricher\"]:
    py_compile.compile(f\"$VPS_CONTAINER_DIR/{mod}.py\", doraise=True)
    print(f\"  ✓ {mod}.py OK\")
'"

# 4. Restart dashboard
echo "🔄 Restarting dashboard..."
ssh "$VPS_HOST" "fuser -k 5004/tcp 2>/dev/null; systemctl restart leads-dashboard.service"
sleep 3

# 5. Status
echo ""
echo "✅ Deploy complete!"
echo ""
echo "Dashboard: https://leads.patdilet.dev (user: patdilet)"
echo ""
echo "Status:"
ssh "$VPS_HOST" "
    echo '--- Dashboard ---'
    systemctl status leads-dashboard.service --no-pager -l | head -3
    echo ''
    echo '--- Last commit on VPS ---'
    cd $VPS_HOST_DIR && git log -1 --oneline
    echo ''
    echo '--- Pipeline ---'
    curl -s -u patdilet:patdilet2026 https://leads.patdilet.dev/api/stats | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(f\"Leads: {d[\\\"leads\\\"][\\\"total_leads\\\"]} | Pending: {d[\\\"outreach\\\"][\\\"pending_approval\\\"]} | Campaigns: {len(d.get(\\\"campaigns\\\", []))}\")
'
"
