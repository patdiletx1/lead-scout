#!/bin/bash
# ClientScout — Deploy to VPS (Hetzner 91.99.157.147)
# Usage: ./deploy/deploy.sh

set -e

VPS_HOST="root@91.99.157.147"
VPS_DIR="/opt/data/home/lead_scout"
CONTAINER="hermes"
VENV_PYTHON="/opt/hermes/.venv/bin/python3"

echo "🔄 Deploying ClientScout to VPS..."

# 1. Create directory on VPS
ssh "$VPS_HOST" "mkdir -p $VPS_DIR/deploy"

# 2. Copy Python files
echo "📦 Copying Python files..."
scp ../*.py "$VPS_HOST:$VPS_DIR/"

# 3. Copy deploy files
echo "📦 Copying infra files..."
scp ./* "$VPS_HOST:$VPS_DIR/deploy/"

# 4. Compile check (syntax validation inside hermes)
echo "🔍 Validating syntax..."
ssh "$VPS_HOST" "docker exec $CONTAINER $VENV_PYTHON -c '
import sys; sys.path.insert(0, \"$VPS_DIR\")
import py_compile
for mod in [\"leads_db\", \"lead_scoring\", \"scout_profile\"]:
    py_compile.compile(\"$VPS_DIR/\" + mod + \".py\", doraise=True)
    print(f\"  ✓ {mod}.py OK\")
'"

# 5. Install systemd services
echo "🔧 Installing systemd services..."
ssh "$VPS_HOST" "
    cp $VPS_DIR/deploy/leads-dashboard.service /etc/systemd/system/
    cp $VPS_DIR/deploy/leads-discovery.service /etc/systemd/system/
    cp $VPS_DIR/deploy/leads-discovery.timer /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable leads-dashboard.service
    systemctl enable leads-discovery.timer
"

# 6. Restart dashboard
echo "🔄 Restarting dashboard..."
ssh "$VPS_HOST" "systemctl restart leads-dashboard.service"

# 7. Start timer
echo "⏰ Starting discovery timer..."
ssh "$VPS_HOST" "systemctl start leads-discovery.timer"

# 8. Show status
echo ""
echo "✅ Deploy complete!"
echo ""
echo "Dashboard: https://leads.patdilet.dev (user: patdilet)"
echo "Status:"
ssh "$VPS_HOST" "
    echo '--- Dashboard ---'
    systemctl status leads-dashboard.service --no-pager -l | head -5
    echo ''
    echo '--- Timer ---'
    systemctl list-timers | grep leads
"

echo ""
echo "📋 Post-deploy checklist:"
echo "  1. Add Caddy config snippet (deploy/Caddyfile_snippet.conf) to /etc/caddy/Caddyfile"
echo "  2. Run: systemctl reload caddy"
echo "  3. Test: curl -u patdilet:PASSWORD https://leads.patdilet.dev/api/stats"
echo "  4. Manual test: docker exec hermes $VENV_PYTHON $VPS_DIR/lead_scraper.py discover"
