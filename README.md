# Calendar Agent Backend

Matrix-first corporate calendar backend for Hermes/Matrix agents with Radicale as CalDAV storage.

This MVP intentionally does **not** include Hermes. Hermes or another Matrix bot can call this service in two ways:

- as a thin transport through `POST /agent/message`, where the service uses an OpenAI-compatible LLM to parse natural-language Russian messages;
- as structured REST/MCP tools, if the external agent already performs NLU itself.

## Components

- `calendar-service`: Python/FastAPI backend with `/agent/message`, REST API, MCP tools, SQLite state, LLM orchestration and calendar logic.
- `radicale`: built-in Radicale CalDAV server for local testing and simple deployment.
- `SQLite`: employees, logical meetings, event copies, pending actions, reminders, outbox and audit log.

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
ALLOWED_MATRIX_SERVERS=org1.company.ru,org2.company.ru
RADICALE_HTPASSWD_PASSWORD=change-me
RADICALE_PASSWORD=change-me
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=...
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
curl http://localhost:8080/health
```

## Demo seed

Demo data is disabled by default. Run it manually:

```bash
docker compose exec calendar-service python -m app.cli demo-seed
```

It creates:

- `@ivanov:org1.company.ru`
- `@petrova:org1.company.ru`
- `@sidorov:org2.company.ru`

and a couple of demo events.

## REST authentication

All tool endpoints require:

```http
Authorization: Bearer <AGENT_API_TOKEN>
```

Admin endpoints require:

```http
Authorization: Bearer <ADMIN_API_TOKEN>
```


## Natural-language agent endpoint

The main endpoint for a thin Hermes/Matrix bot is:

```http
POST /agent/message
Authorization: Bearer <AGENT_API_TOKEN>
```

Request:

```json
{
  "matrix_id": "@ivanov:org1.company.ru",
  "display_name": "Иванов Иван",
  "conversation_id": "matrix-room-id",
  "message": "Запланируй встречу с Петровой завтра после обеда на 30 минут, напомни за час"
}
```

What happens internally:

1. the employee is auto-provisioned by Matrix ID if needed;
2. the service sends the user message to the configured OpenAI-compatible LLM;
3. the LLM returns a strict JSON intent;
4. `calendar_service` validates the intent and calls its internal calendar tools;
5. calendar-changing operations create a pending action and return a Russian confirmation question;
6. when the user replies `да`, the service confirms the pending action and writes to Radicale.

Example:

```bash
curl -s -X POST "$BASE/agent/message" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "matrix_id": "@ivanov:org1.company.ru",
    "display_name": "Иванов Иван",
    "message": "Поставь мне завтра с 10 до 11 подготовку отчета, напомни за 30 минут"
  }' | jq
```

The response contains `reply` that Hermes should send back to Matrix. If the response asks for confirmation, forward the next user message to the same endpoint:

```bash
curl -s -X POST "$BASE/agent/message" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "matrix_id": "@ivanov:org1.company.ru",
    "message": "да"
  }' | jq
```

Local handling of `да`, `нет`, `1`, `2`, `3` is implemented without calling the LLM. This keeps confirmations and slot selection deterministic.

Supported high-level intents in v0.2.1:

- show schedule;
- search employees;
- find free slots;
- create personal events;
- create meetings;
- reschedule events by ID or sufficiently narrow criteria;
- cancel events by ID or sufficiently narrow criteria.

For ambiguous reschedule/cancel requests, the service returns a clarification with candidate meeting IDs.

## Common REST examples

Set shell variables:

```bash
export AGENT_TOKEN="replace-with-random-agent-token"
export ADMIN_TOKEN="replace-with-random-admin-token"
export BASE="http://localhost:8080"
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

### Create event draft

```bash
curl -s -X POST "$BASE/tools/draft-create-event" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "title": "Внедрение календарного агента",
    "participants": [{"matrix_id": "@petrova:org1.company.ru"}],
    "start": "2026-05-04T14:30:00+03:00",
    "end": "2026-05-04T15:00:00+03:00",
    "reminder_minutes": 60
  }' | jq
```

The response contains `id` of pending action. Confirm it:

```bash
curl -s -X POST "$BASE/tools/confirm-pending-action" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "requester_matrix_id": "@ivanov:org1.company.ru",
    "pending_action_id": "<ACTION_ID>",
    "confirm": true
  }' | jq
```

### Enqueue due reminders and read outbox

```bash
curl -s -X POST "$BASE/tools/reminders/enqueue-due" \
  -H "Authorization: Bearer $AGENT_TOKEN" | jq

curl -s "$BASE/tools/outbox" \
  -H "Authorization: Bearer $AGENT_TOKEN" | jq
```

Hermes/Matrix bot should send each outbox message to `matrix_id` and then mark it delivered:

```bash
curl -s -X POST "$BASE/tools/outbox/1/mark-delivered" \
  -H "Authorization: Bearer $AGENT_TOKEN" | jq
```

## Admin API examples

```bash
curl -s "$BASE/admin/employees" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

Update employee timezone/workday:

```bash
curl -s -X PATCH "$BASE/admin/employees/org1_company_ru__ivanov__2a16afca1b" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "timezone": "Europe/Moscow",
    "workday_start": "10:00",
    "workday_end": "19:00",
    "workdays": ["MO", "TU", "WE", "TH", "FR"]
  }' | jq
```

## MCP server

The project includes a working MCP wrapper around the same business logic.

Run inside the container:

```bash
docker compose exec calendar-service python -m app.mcp_server
```

Available tools include:

- `ensure_employee_tool`
- `search_employees_tool`
- `get_schedule_tool`
- `find_free_slots_tool`
- `draft_create_event_tool`
- `draft_reschedule_event_tool`
- `draft_cancel_event_tool`
- `confirm_pending_action_tool`
- `agent_message_tool`

Exact MCP client configuration depends on Hermes' MCP transport support.

## External Radicale

By default docker-compose starts Radicale. To use an external Radicale, update `.env`:

```env
RADICALE_URL=https://calendar.company.ru
RADICALE_USERNAME=calendar-service
RADICALE_PASSWORD=...
```

Then remove or ignore the built-in `radicale` service.

## MVP limitations

- No recurring events.
- No rooms/resources.
- No task integration.
- No web calendar UI.
- No email invitations.
- Organizer-only reschedule/cancel in MVP.
- The built-in LLM orchestrator is intentionally narrow: it parses calendar commands, but it does not directly write to Radicale and all changes still require confirmation.


## Revision notes

This package is an MVP scaffold. The first revision was followed by a repair pass that fixes:
- Dockerfile package installation order;
- direct CalDAV writes to per-employee Radicale paths;
- admin 404 handling for missing employees;
- reminder cleanup/recreation on reschedule/cancel.

For production use, run integration tests with your actual Radicale and Hermes setup.


## Revision notes v0.1.3

Additional senior-review fixes:

- employee calendars are now created during auto-provisioning, not lazily on the first event;
- `calendar_created` audit events are written;
- employee IDs keep a readable prefix but include a short hash suffix to avoid MXID normalization collisions;
- Radicale calendar paths URL-encode Matrix server/localpart components;
- `RADICALE_STRICT_WRITES=true` by default so CalDAV write failures are not silently ignored;
- free-slot duration, max slots and reminder minutes have validation bounds;
- reminder enqueue writes `reminder_sent` audit events;
- outbox reads are ordered and marking a missing message delivered returns 404.


## Revision notes v0.2.1

This revision adds the missing LLM participation layer:

- `POST /agent/message` accepts ordinary Matrix text messages;
- OpenAI-compatible LLM is used for Russian natural-language intent extraction;
- local deterministic handling of confirmations: `да`, `нет`, `1`, `2`, `3`;
- slot-selection pending actions for commands like “найди время после обеда”;
- `agent_message_tool` is available in MCP;
- Hermes can now be a thin Matrix transport: receive Matrix message, call `/agent/message`, send `reply` back to Matrix.

## Revision notes v0.2.1

The conversational runtime now stores structured dialog context in SQLite:

- active dialog state is scoped by `matrix_id + conversation_id`;
- `Да` / `Нет` confirms or cancels the pending action only inside the current conversation;
- `1`, `2`, `3`, `первый`, `второй`, `третий` work for employee selection, event selection and slot selection;
- employee ambiguity is stored as `choose_employee` dialog state;
- event ambiguity for reschedule/cancel is stored as `choose_event` dialog state;
- slot selection is stored as `slot_selection` dialog state;
- reschedule without new time can store `await_reschedule_time` and ask the user for the new time.

Hermes should always pass the Matrix room ID as `conversation_id`. If omitted, the service uses `__default__`, which is suitable only for single-threaded tests.
