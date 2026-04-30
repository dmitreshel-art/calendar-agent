#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
USER_NAME="${RADICALE_HTPASSWD_USER:-calendar-service}"
USER_PASSWORD="${RADICALE_HTPASSWD_PASSWORD:-change-me}"
mkdir -p "$ROOT_DIR/data/radicale"
if command -v htpasswd >/dev/null 2>&1; then
  htpasswd -Bbc "$ROOT_DIR/data/radicale/users" "$USER_NAME" "$USER_PASSWORD"
else
  echo "htpasswd is required. Install apache2-utils/httpd-tools or create data/radicale/users manually." >&2
  exit 1
fi
