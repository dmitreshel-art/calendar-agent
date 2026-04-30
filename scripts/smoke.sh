#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${BASE:-http://localhost:8080}"
ENV_FILE="$ROOT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

AGENT_TOKEN="${AGENT_TOKEN:-${AGENT_API_TOKEN:-}}"
if [[ -z "$AGENT_TOKEN" ]]; then
  echo "AGENT_TOKEN or AGENT_API_TOKEN must be set." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required for smoke JSON parsing." >&2
  exit 1
fi

suffix="${SMOKE_SUFFIX:-$(date +%s)}"
user1="@smoke${suffix}a:org1.company.ru"
user2="@smoke${suffix}b:org1.company.ru"
start_time="2026-05-04T14:30:00+03:00"
end_time="2026-05-04T15:00:00+03:00"

json_post() {
  local path="$1"
  local payload="$2"
  curl -fsS -X POST "$BASE$path" \
    -H "Authorization: Bearer $AGENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$payload"
}

json_get() {
  local path="$1"
  curl -fsS "$BASE$path" -H "Authorization: Bearer $AGENT_TOKEN"
}

printf '1/8 health...\n'
curl -fsS "$BASE/health" >/tmp/calendar-agent-smoke-health.json

printf '2/8 ensure employees...\n'
json_post "/tools/ensure-employee" "{\"matrix_id\":\"$user1\",\"display_name\":\"Smoke User A\"}" >/tmp/calendar-agent-smoke-ensure1.json
json_post "/tools/ensure-employee" "{\"matrix_id\":\"$user2\",\"display_name\":\"Smoke User B\"}" >/tmp/calendar-agent-smoke-ensure2.json

printf '3/8 draft event...\n'
json_post "/tools/draft-create-event" "{\"requester_matrix_id\":\"$user1\",\"title\":\"Smoke pilot event\",\"participants\":[{\"matrix_id\":\"$user2\"}],\"start\":\"$start_time\",\"end\":\"$end_time\",\"reminder_minutes\":60}" >/tmp/calendar-agent-smoke-draft.json
pending_id="$(python3 - <<'PY'
import json
print(json.load(open('/tmp/calendar-agent-smoke-draft.json'))['id'])
PY
)"

printf '4/8 confirm event...\n'
json_post "/tools/confirm-pending-action" "{\"requester_matrix_id\":\"$user1\",\"pending_action_id\":\"$pending_id\",\"confirm\":true}" >/tmp/calendar-agent-smoke-confirm.json
python3 - <<'PY'
import json, sys
result = json.load(open('/tmp/calendar-agent-smoke-confirm.json'))
assert result['status'] == 'confirmed', result
assert result['meeting_id'], result
PY

printf '5/8 own schedule exposes details...\n'
json_post "/tools/get-schedule" "{\"requester_matrix_id\":\"$user1\",\"start\":\"2026-05-04T00:00:00+03:00\",\"end\":\"2026-05-05T00:00:00+03:00\"}" >/tmp/calendar-agent-smoke-own-schedule.json
python3 - <<'PY'
import json
rows = json.load(open('/tmp/calendar-agent-smoke-own-schedule.json'))
assert rows, rows
assert rows[0]['title'] == 'Smoke pilot event', rows
assert rows[0]['meeting_id'] is not None, rows
PY

printf '6/8 free/busy privacy hides details for other employee...\n'
json_post "/tools/get-schedule" "{\"requester_matrix_id\":\"$user2\",\"target_matrix_id\":\"$user1\",\"start\":\"2026-05-04T00:00:00+03:00\",\"end\":\"2026-05-05T00:00:00+03:00\"}" >/tmp/calendar-agent-smoke-other-schedule.json
python3 - <<'PY'
import json
rows = json.load(open('/tmp/calendar-agent-smoke-other-schedule.json'))
assert rows, rows
assert rows[0]['title'] == 'Занят', rows
assert rows[0]['meeting_id'] is None, rows
assert rows[0]['participants'] == [], rows
PY

printf '7/8 reminders enqueue/outbox...\n'
json_post "/tools/reminders/enqueue-due" "{}" >/tmp/calendar-agent-smoke-reminders.json || true
json_get "/tools/outbox" >/tmp/calendar-agent-smoke-outbox.json

printf '8/8 Radicale .ics files exist...\n'
ics_count="$(find "$ROOT_DIR/data/radicale/collections" -type f -name '*.ics' ! -path '*/.Radicale.cache/*' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$ics_count" -lt 2 ]]; then
  echo "Expected at least 2 real .ics files, found $ics_count" >&2
  find "$ROOT_DIR/data/radicale" -type f -name '*.ics' -print >&2 || true
  exit 1
fi

printf '\nSmoke test PASS. Created meeting %s for %s and %s; real .ics files: %s\n' \
  "$(python3 - <<'PY'
import json
print(json.load(open('/tmp/calendar-agent-smoke-confirm.json'))['meeting_id'])
PY
)" "$user1" "$user2" "$ics_count"
