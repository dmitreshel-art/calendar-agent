#!/bin/bash
# ── Deploy script for Calendar Agent + Hermes ─────────────
# Run this on the target server for first-time setup.
set -e

COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$COMPOSE_DIR"

echo "=== Calendar Agent + Hermes Deployment ==="
echo ""

# 1. Create data directories
echo "[1/4] Creating data directories..."
mkdir -p "$COMPOSE_DIR/data/hermes"
mkdir -p "$COMPOSE_DIR/data/calendar_service"
mkdir -p "$COMPOSE_DIR/data/radicale"

# 2. Copy initial SOUL.md if not present
if [ ! -f "$COMPOSE_DIR/data/hermes/SOUL.md" ]; then
    echo "[2/4] Installing SOUL.md..."
    cp "$COMPOSE_DIR/hermes-home/SOUL.md" "$COMPOSE_DIR/data/hermes/SOUL.md"
else
    echo "[2/4] SOUL.md already exists, keeping it"
fi

# 3. Build Hermes image with Matrix E2EE + local STT/TTS deps
echo "[3/4] Building Hermes image..."
docker compose build hermes

# 4. Build calendar-service image
echo "[4/4] Building calendar-service image..."
docker compose build calendar-service

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo ""
echo "  1. First-time Hermes config (interactive wizard):"
echo "     docker compose run --rm hermes setup"
echo ""
echo "  2. Apply calendar-specific Hermes settings:"
echo "     ./deploy/configure-hermes-calendar.sh"
echo "     This writes MCP, Matrix behavior, local ru STT and local Piper TTS"
echo "     into data/hermes/config.yaml."
echo ""
echo "  3. Start all services:"
echo "     docker compose up -d"
echo ""
echo "  4. Watch logs:"
echo "     docker compose logs -f hermes"
