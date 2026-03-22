#!/bin/bash
# setup_vm.sh — Run once on a fresh GCP e2-micro VM (Debian 12) to install
# all dependencies and configure cron for both sliding windows.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_LOG="$PROJECT_DIR/cron.log"
UV="$HOME/.local/bin/uv"

echo "=== Rotten Tomatoes scraper — VM setup ==="
echo "Project dir: $PROJECT_DIR"

# ── 1. System packages ────────────────────────────────────────────────────────
echo ""
echo "[1/4] Installing system packages (chromium, git)..."
sudo apt-get update -qq
sudo apt-get install -y chromium git

CHROME_BIN="$(which chromium)"
echo "      Chromium found at: $CHROME_BIN"

# ── 2. uv ─────────────────────────────────────────────────────────────────────
echo ""
echo "[2/4] Installing uv..."
if command -v uv &>/dev/null; then
    echo "      uv already installed, skipping."
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Ensure uv is on PATH for the rest of this script
export PATH="$HOME/.local/bin:$PATH"

# ── 3. Python dependencies ────────────────────────────────────────────────────
echo ""
echo "[3/4] Installing Python dependencies via uv..."
cd "$PROJECT_DIR"
uv sync

# ── 4. Cron jobs ──────────────────────────────────────────────────────────────
echo ""
echo "[4/4] Installing cron jobs..."

# Remove any existing RT scraper cron entries, then add fresh ones.
(crontab -l 2>/dev/null | grep -v "rotten_tomatoes.py" | grep -v "backup_db.sh" | grep -v "cleanup_csv.sh" || true) | crontab -

(crontab -l 2>/dev/null; cat <<EOF

# Rotten Tomatoes scraper — added by setup_vm.sh
CHROME_BIN=$CHROME_BIN
*/5 * * * * cd $PROJECT_DIR && $UV run python rotten_tomatoes.py --window hour >> $CRON_LOG 2>&1
0 */6 * * * cd $PROJECT_DIR && $UV run python rotten_tomatoes.py --window day  >> $CRON_LOG 2>&1
0 3 * * * $PROJECT_DIR/deploy/backup_db.sh >> $CRON_LOG 2>&1
0 4 * * * $PROJECT_DIR/deploy/cleanup_csv.sh >> $CRON_LOG 2>&1
EOF
) | crontab -

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Cron jobs installed:"
crontab -l | grep -A3 "Rotten Tomatoes"
echo ""
echo "The hour window will run every 5 minutes."
echo "The day window will run every 6 hours."
echo "DB backup runs daily at 3:00 AM."
echo "CSV cleanup runs daily at 4:00 AM."
echo "Logs → $CRON_LOG"
echo ""
echo "To test immediately, run:"
echo "  cd $PROJECT_DIR && CHROME_BIN=$CHROME_BIN $UV run python rotten_tomatoes.py --window hour"
