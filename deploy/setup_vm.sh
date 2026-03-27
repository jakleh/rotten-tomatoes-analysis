#!/bin/bash
# setup_vm.sh — Run once on a fresh GCP e2-micro VM (Debian 12) to install
# all dependencies, configure cron, and set up log shipping to Cloud Logging.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_LOG="$PROJECT_DIR/cron.log"
UV="$HOME/.local/bin/uv"

echo "=== Rotten Tomatoes scraper — VM setup ==="
echo "Project dir: $PROJECT_DIR"

# ── 1. System packages ────────────────────────────────────────────────────────
echo ""
echo "[1/6] Installing system packages (chromium, git)..."
sudo apt-get update -qq
sudo apt-get install -y chromium git

CHROME_BIN="$(which chromium)"
echo "      Chromium found at: $CHROME_BIN"

# ── 2. uv ─────────────────────────────────────────────────────────────────────
echo ""
echo "[2/6] Installing uv..."
if command -v uv &>/dev/null; then
    echo "      uv already installed, skipping."
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Ensure uv is on PATH for the rest of this script
export PATH="$HOME/.local/bin:$PATH"

# ── 3. Scraper dependencies ───────────────────────────────────────────────────
echo ""
echo "[3/6] Installing scraper dependencies via uv..."
cd "$PROJECT_DIR"
uv sync

# ── 4. Cron jobs ──────────────────────────────────────────────────────────────
echo ""
echo "[4/6] Installing cron jobs..."

# Remove the entire RT scraper block (everything between markers), then add fresh.
(crontab -l 2>/dev/null | sed '/^# Rotten Tomatoes scraper/,/^$/d' | sed '/^CHROME_BIN=/d' || true) | crontab -

(crontab -l 2>/dev/null; cat <<EOF

# Rotten Tomatoes scraper — added by setup_vm.sh
CHROME_BIN=$CHROME_BIN
*/5 * * * * cd $PROJECT_DIR && $UV run python rotten_tomatoes.py --window hour >> $CRON_LOG 2>&1
0 */6 * * * cd $PROJECT_DIR && $UV run python rotten_tomatoes.py --window day  >> $CRON_LOG 2>&1
0 3 * * * $PROJECT_DIR/deploy/backup_db.sh >> $CRON_LOG 2>&1
0 4 * * * $PROJECT_DIR/deploy/cleanup_csv.sh >> $CRON_LOG 2>&1
# End Rotten Tomatoes scraper
EOF
) | crontab -

# ── 5. Ops Agent (log shipping to Cloud Logging) ────────────────────────────
echo ""
echo "[5/6] Configuring Ops Agent for Cloud Logging..."

if ! systemctl is-active --quiet google-cloud-ops-agent; then
    echo "      Installing Ops Agent..."
    curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
    sudo bash add-google-cloud-ops-agent-repo.sh --also-install
    rm -f add-google-cloud-ops-agent-repo.sh
else
    echo "      Ops Agent already running."
fi

sudo cp "$PROJECT_DIR/deploy/ops-agent-config.yaml" /etc/google-cloud-ops-agent/config.yaml
sudo systemctl restart google-cloud-ops-agent
echo "      Ops Agent configured and restarted."

# ── 6. Datasette systemd service ─────────────────────────────────────────────
echo ""
echo "[6/6] Setting up rt-datasette systemd service..."
sudo cp "$PROJECT_DIR/deploy/rt-datasette.service" /etc/systemd/system/rt-datasette.service
sudo systemctl daemon-reload
sudo systemctl enable rt-datasette
sudo systemctl restart rt-datasette
echo "      rt-datasette service enabled and started."

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Cron jobs installed:"
crontab -l | sed -n '/^# Rotten Tomatoes scraper/,/^# End Rotten Tomatoes/p'
echo ""
echo "The hour window will run every 5 minutes."
echo "The day window will run every 6 hours."
echo "DB backup runs daily at 3:00 AM."
echo "CSV cleanup runs daily at 4:00 AM."
echo "Scraper logs → $CRON_LOG"
echo ""
echo "Datasette status:"
systemctl is-active rt-datasette && echo "  rt-datasette is running on 127.0.0.1:8001" || echo "  WARNING: rt-datasette failed to start — check: journalctl -u rt-datasette"
echo "Datasette logs → $PROJECT_DIR/datasette.log"
echo "Access via SSH tunnel: ssh -L 8001:127.0.0.1:8001 <vm-ip>"
echo ""
echo "To test the scraper immediately, run:"
echo "  cd $PROJECT_DIR && CHROME_BIN=$CHROME_BIN $UV run python rotten_tomatoes.py --window hour"
