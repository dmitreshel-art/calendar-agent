#!/bin/bash
# Merge calendar-agent runtime settings into the real Hermes config.
# Run after: docker compose run --rm hermes setup
set -euo pipefail

COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$COMPOSE_DIR"

CONFIG="$COMPOSE_DIR/data/hermes/config.yaml"
FRAGMENT="$COMPOSE_DIR/hermes-home/config.calendar.fragment.yaml"
SOUL_SRC="$COMPOSE_DIR/hermes-home/SOUL.md"
SOUL_DST="$COMPOSE_DIR/data/hermes/SOUL.md"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: $CONFIG not found. Run first: docker compose run --rm hermes setup" >&2
  exit 1
fi

if [ ! -f "$FRAGMENT" ]; then
  echo "ERROR: $FRAGMENT not found" >&2
  exit 1
fi

mkdir -p "$COMPOSE_DIR/data/hermes"

if [ -f "$SOUL_SRC" ]; then
  cp "$SOUL_SRC" "$SOUL_DST"
  echo "Updated SOUL.md: $SOUL_DST"
fi

BACKUP="$CONFIG.bak.$(date -u +%Y%m%d-%H%M%S)"
cp "$CONFIG" "$BACKUP"
echo "Backup: $BACKUP"

# Use Python from the Hermes image so PyYAML availability matches Hermes runtime.
# The script updates only calendar-specific keys and preserves model/provider/API setup.
docker compose run --rm \
  --entrypoint /opt/hermes/.venv/bin/python \
  hermes - "$CONFIG" "$FRAGMENT" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except Exception as exc:
    raise SystemExit(f"PyYAML is not available in Hermes image: {exc}")

config_path = Path(sys.argv[1])
fragment_path = Path(sys.argv[2])

with config_path.open("r", encoding="utf-8") as f:
    config = yaml.safe_load(f) or {}
with fragment_path.open("r", encoding="utf-8") as f:
    fragment = yaml.safe_load(f) or {}

if not isinstance(config, dict):
    raise SystemExit(f"Config is not a YAML mapping: {config_path}")
if not isinstance(fragment, dict):
    raise SystemExit(f"Fragment is not a YAML mapping: {fragment_path}")

# Merge only known calendar-runtime keys. Do not touch model/provider/API keys.
for key in ("mcp_servers", "group_sessions_per_user", "matrix", "stt", "tts", "voice"):
    if key in fragment:
        config[key] = fragment[key]

# Keep existing toolsets, but ensure TTS is available for voice replies.
existing_toolsets = config.get("toolsets")
if isinstance(existing_toolsets, list):
    if "tts" not in existing_toolsets:
        existing_toolsets.append("tts")
elif "toolsets" in fragment and isinstance(fragment["toolsets"], list):
    config["toolsets"] = fragment["toolsets"]

with config_path.open("w", encoding="utf-8") as f:
    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

print("Updated:")
print(f"  {config_path}")
print("Applied calendar settings:")
print("  MCP: http://calendar-service:8090/mcp/mcp")
print("  Matrix: require_mention=true, auto_thread=true")
print("  STT: local faster-whisper small, language=ru")
print("  TTS: local Piper ru_RU-irina-medium")
PY

echo ""
echo "Done. Restart Hermes after services are up:"
echo "  docker compose restart hermes"
