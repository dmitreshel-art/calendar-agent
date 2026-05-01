# Calendar Agent Backend

Matrix-first calendar backend for Hermes/Matrix agents with Radicale as CalDAV storage.

Calendar Agent is now a **structured backend/tool server**, not a second conversational LLM agent. Hermes owns natural-language understanding and dialogue. This service owns calendar business logic, permissions, audit, persistence, reminders, REST API, and MCP tools.

## Architecture

```text
Matrix / Element / Telegram
  ↓
Hermes Agent
  ↓ MCP tools or REST /tools/* and /admin/*
calendar-service
  ↓ CalDAV
Radicale
```

## Components

- `calendar-service`: Python/FastAPI backend with REST API, MCP tools, SQLite state, permissions, reminders, audit, and Radicale integration.
- `radicale`: built-in Radicale CalDAV server for local testing and simple deployment.
- `SQLite`: employees, logical meetings, event copies, pending actions, reminders, outbox, and audit log.

## Quick start

```bash
cp .env.example .env
```

Edit `.env` and replace tokens:

```bash
openssl rand -hex 32
```

Set at least:

```env
AGENT_API_TOKEN=...
ADMIN_API_TOKEN=...
ALLOWED_MATRIX_SERVERS=org1.company.ru,org2.company.ru,org-main.company.ru
RADICALE_HTPASSWD_PASSWORD=...
RADICALE_PASSWORD=...
```

Create Radicale htpasswd file:

```bash
./scripts/init-radicale-users.sh
```

Start:

```bash
docker compose up -d --build
```

Check health:

```bash
curl http://localhost:8090/health
```

## Operating model

Hermes should interpret user messages and call structured tools directly.

Calendar-changing operations are still two-step:

1. Hermes calls a `draft_*` tool/endpoint.
2. Calendar Agent creates a `pending_action` but does not mutate Radicale yet.
3. Hermes summarizes the proposed action and asks the user for confirmation.
4. If the user confirms, Hermes calls `confirm_pending_action_tool` or `/tools/confirm-pending-action`.
5. Calendar Agent writes to SQLite/Radicale and records audit events.

This keeps natural-language reasoning in one place while preserving backend-side confirmation, permissions, and audit.

## REST authentication

Tool endpoints require:

```http
Authorization: Bearer <AGENT_API_TOKEN>
```

Admin endpoints require:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```

## Common REST examples

Set shell variables:

```bash
export AGENT_TOKEN="..."
export ADMIN_TOKEN="..."
export BASE="http://localhost:8090"
```

### Ensure employee

```bash
curl -s -X POST "$BASE/tools/ensure-employee" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "matrix_id": "@ivanov:org1.company.ru",
    "display_name": "Иванов Иван"
  }' | jq
```

### Get schedule

```bash
curl -s -X POST "$BASE/tools/get-schedule" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "start": "2026-05-01T00:00:00+03:00",
    "end": "2026-05-08T00:00:00+03:00"
  }' | jq
```

For another employee, pass `target_matrix_id`; the response exposes free/busy only.

### Find free slots

```bash
curl -s -X POST "$BASE/tools/find-free-slots" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "participants": [{"matrix_id": "@petrova:org1.company.ru"}],
    "duration_minutes": 30
  }' | jq
```

### Create event draft and confirm

```bash
ACTION_ID=$(curl -s -X POST "$BASE/tools/draft-create-event" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "title": "Внедрение календарного агента",
    "participants": [{"matrix_id": "@petrova:org1.company.ru"}],
    "start": "2026-05-04T14:30:00+03:00",
    "end": "2026-05-04T15:00:00+03:00",
    "reminder_minutes": 60
  }' | jq -r .id)

curl -s -X POST "$BASE/tools/confirm-pending-action" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"requester_matrix_id\":\"@ivanov:org1.company.ru\",\"pending_action_id\":\"$ACTION_ID\",\"confirm\":true}" | jq
```

### Clear own calendar draft

```bash
curl -s -X POST "$BASE/tools/draft-clear-my-calendar" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "start": "2026-05-01T00:00:00+03:00",
    "end": "2026-06-01T00:00:00+03:00"
  }' | jq
```

### Enqueue due reminders and read outbox

```bash
curl -s -X POST "$BASE/tools/reminders/enqueue-due" \
  -H "Authorization: Bearer $AGENT_TOKEN" | jq

curl -s "$BASE/tools/outbox" \
  -H "Authorization: Bearer $AGENT_TOKEN" | jq
```

Hermes/Matrix delivery should send each outbox message to `matrix_id` and then mark it delivered.

## Admin API examples

```bash
curl -s "$BASE/admin/employees" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

Update employee role/timezone/workday:

```bash
curl -s -X PATCH "$BASE/admin/employees/<EMPLOYEE_ID>" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"calendar_admin"}' | jq
```

Create admin clear-calendar draft:

```bash
curl -s -X POST "$BASE/admin/draft-clear-calendar" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "admin_matrix_id": "@admin:org1.company.ru",
    "target_matrix_id": "@ivanov:org1.company.ru",
    "start": "2026-05-01T00:00:00+03:00",
    "end": "2026-06-01T00:00:00+03:00",
    "reason": "pilot data cleanup"
  }' | jq
```

## MCP tools

The MCP server exposes structured tools only:

- `ensure_employee_tool`
- `search_employees_tool`
- `get_schedule_tool`
- `find_free_slots_tool`
- `draft_create_event_tool`
- `draft_reschedule_event_tool`
- `draft_cancel_event_tool`
- `draft_clear_my_calendar_tool`
- `list_pending_actions_tool`
- `confirm_pending_action_tool`
- `deliver_notifications_tool`
- `admin_list_employees_tool`
- `admin_patch_employee_tool`
- `admin_draft_clear_calendar_tool`
- `admin_audit_log_tool`

## Permissions model

- Regular user: full access to own calendar; free/busy only for others.
- Organizer: can reschedule/cancel own meetings through user tools.
- Calendar admin: can use admin tools such as clearing another employee calendar.
- Every calendar mutation goes through pending actions and audit logging.

## Demo seed

Demo data is disabled by default. Run it manually:

```bash
docker compose exec calendar-service python -m app.cli demo-seed
```

## Development

```bash
cd services/calendar-service
pytest -q
```
