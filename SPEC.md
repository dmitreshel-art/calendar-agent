# Calendar Agent Backend — MVP Specification

## Purpose

This project implements a backend for a corporate calendar agent. Employees interact with a Matrix/Hermes bot. The backend stores calendars in Radicale/CalDAV and exposes a natural-language `/agent/message` endpoint, structured REST API, and MCP tools.

Hermes is not included in the first Docker package. In v0.2.1 Hermes can be a thin Matrix transport that forwards `matrix_id + message` to `/agent/message` and sends the returned `reply` back to Matrix.

## Architecture

```text
Matrix / Element
  ↓
Hermes / Matrix bot
  ↓ /agent/message, REST or MCP
calendar_service
  ↓ CalDAV
Radicale
```

`calendar_service` owns business logic. Radicale is storage only.

## MVP scope

Implemented in MVP:

- employee auto-provisioning by Matrix ID;
- trusted Matrix federation through homeserver allowlist;
- personal events;
- meetings between employees;
- meeting as event copy in each participant calendar;
- schedule retrieval;
- free slot search;
- reschedule/cancel through pending actions;
- confirmation for every calendar change;
- Matrix reminder outbox;
- admin REST API;
- audit log;
- LLM-based natural-language `/agent/message`;
- REST API;
- MCP tools;
- demo seed.

Out of MVP:

- Hermes container;
- recurring events;
- tasks;
- rooms/resources;
- web UI;
- email invitations;
- LDAP/Keycloak sync;
- broad non-calendar general assistant behavior.

## User model

Employees are created automatically on first request:

```text
Matrix ID → validate homeserver → employee record → Radicale calendar path
```

The Matrix ID is the identity key. In federated Matrix, localpart is not enough:

```text
@ivanov:org1.company.ru != @ivanov:org2.company.ru
```

Internal employee ID example:

```text
@ivanov:org1.company.ru → org1_company_ru__ivanov__<hash>
```

Calendar path example:

```text
/calendars/org1.company.ru/ivanov/default
```

## Admission model

A user is admitted if their Matrix homeserver is in `ALLOWED_MATRIX_SERVERS`. Unknown homeservers are rejected when `DISABLE_UNKNOWN_HOMESERVERS=true`.

Optional `BLOCKED_MATRIX_USERS` prevents service accounts from being provisioned.

## Employee settings

Each employee has:

- timezone;
- workday start;
- workday end;
- workdays;
- status.

Defaults come from `.env` and can be changed via admin API.

## Meeting model

A logical meeting is stored in SQLite and materialized as copies in Radicale:

```text
Meeting
  ├─ EventCopy for participant A
  ├─ EventCopy for participant B
  └─ EventCopy for participant C
```

This avoids reliance on email invites and calendar client RSVP behavior.

## Confirmation model

All calendar changes create `pending_actions` first. No Radicale change is performed until confirmation.

Pending action types:

- `create_event`
- `reschedule_event`
- `cancel_event`
- `slot_selection` for conversational selection of one of the proposed free slots. Selecting a slot creates a second `create_event` pending action that still requires confirmation.

## Free/busy privacy

When requesting another employee's schedule, only free/busy information is exposed. Event titles, descriptions and participants are hidden.

## Free slot search defaults

When no search period is supplied, search over the next five working days. Return up to three slots by default.

## Reminders

Because employees use Matrix as their main interface, reminders are not only CalDAV alarms. The service creates reminder records and an outbox of Matrix messages.

A Matrix/Hermes worker should:

1. call `/tools/reminders/enqueue-due`;
2. call `/tools/outbox`;
3. send each body to the given `matrix_id`;
4. call `/tools/outbox/{id}/mark-delivered`.

Supported reminder model:

- default reminder: 15 minutes;
- custom reminder minutes/hours/day can be extracted by the built-in LLM endpoint into `reminder_minutes`;
- structured REST/MCP clients can pass `reminder_minutes` directly;
- `no_reminder=true` disables reminder.

## REST authentication

Two static Bearer tokens:

- `AGENT_API_TOKEN` for tool endpoints;
- `ADMIN_API_TOKEN` for admin endpoints.

Generate tokens with:

```bash
openssl rand -hex 32
```

## Audit

Audit is stored in SQLite table `audit_log` and container logs.

Important event types:

- `employee_auto_provisioned`
- `calendar_created`
- `event_draft_created`
- `event_created`
- `event_rescheduled`
- `event_cancelled`
- `reminder_sent`
- `employee_blocked`
- `employee_unblocked`
- `admin_employee_updated`

Full user messages and full LLM traces are intentionally not stored in MVP audit.


## LLM orchestration

The service includes a narrow LLM layer for calendar commands. It is configured through:

```env
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_API_KEY=...
LLM_MODEL=...
LLM_TIMEOUT_SECONDS=30
```

The LLM does not mutate calendars. It only converts a user message into a JSON intent. Business logic then validates the intent and creates pending actions.

Main endpoint:

- `POST /agent/message`

Input:

```json
{
  "matrix_id": "@ivanov:org1.company.ru",
  "display_name": "Иванов Иван",
  "conversation_id": "matrix-room-id",
  "message": "Запланируй встречу с Петровой завтра после обеда на 30 минут"
}
```

Output:

```json
{
  "status": "slot_selection",
  "reply": "Нашел подходящие варианты: ... Какой выбрать?",
  "pending_action_id": "..."
}
```

The external Matrix/Hermes transport should send `reply` to the user and forward the next user message back to `/agent/message`. Confirmations and simple slot selections are handled deterministically without another LLM call.

Supported intents in v0.2.1:

- `get_schedule`;
- `search_employees`;
- `find_free_slots`;
- `create_event`;
- `reschedule_event`;
- `cancel_event`;
- `unknown`.

If reschedule/cancel is ambiguous, the service returns candidate meeting IDs and asks the user to clarify.

## REST API

Agent endpoint:

- `POST /agent/message`

Tool endpoints:

- `POST /tools/ensure-employee`
- `POST /tools/search-employees`
- `POST /tools/get-schedule`
- `POST /tools/find-free-slots`
- `POST /tools/draft-create-event`
- `POST /tools/draft-reschedule-event`
- `POST /tools/draft-cancel-event`
- `POST /tools/confirm-pending-action`
- `GET /tools/pending-actions`
- `POST /tools/reminders/enqueue-due`
- `GET /tools/outbox`
- `POST /tools/outbox/{message_id}/mark-delivered`

Admin endpoints:

- `GET /admin/employees`
- `GET /admin/employees/{id}`
- `PATCH /admin/employees/{id}`
- `POST /admin/employees/{id}/block`
- `POST /admin/employees/{id}/unblock`
- `GET /admin/audit-log`

Service endpoints:

- `GET /health`
- `GET /version`

## MCP tools

MCP tools wrap the same logic as REST endpoints:

- `ensure_employee_tool`
- `search_employees_tool`
- `get_schedule_tool`
- `find_free_slots_tool`
- `draft_create_event_tool`
- `draft_reschedule_event_tool`
- `draft_cancel_event_tool`
- `confirm_pending_action_tool`
- `agent_message_tool`

## Language

User-facing text is Russian. Code, API fields, logs and event type names are English.

## Future improvements

- direct Matrix sending from reminder worker;
- Hermes Docker profile;
- LDAP/Keycloak sync;
- PostgreSQL option;
- recurring events;
- richer attendee status model;
- external notification webhooks;
- production-grade CalDAV collection management;
- OAuth2/JWT service authentication.


## Senior-review implementation notes

Calendar collections are created during auto-provisioning. Radicale writes are strict by default, so a failed CalDAV write causes the API call to fail rather than silently diverging from SQLite. Employee IDs use a readable prefix plus a short hash suffix to prevent collisions in federated Matrix environments.


## Implementation notes v0.2.1

The v0.2.1 revision fixes the architectural gap where LLM settings existed but no LLM runtime was present. The backend now supports both modes:

1. conversational mode through `/agent/message`;
2. structured tool mode through REST/MCP.

This keeps Hermes optional in the Docker package while still allowing real Matrix users to send natural-language calendar commands once a thin Matrix transport is connected.

## Implementation notes v0.2.1

The agent runtime includes a structured dialog state table. It does not store full chat history. It stores only the current expected answer for a given `matrix_id + conversation_id` pair:

- `confirm_pending_action` — waiting for yes/no confirmation;
- `slot_selection` — waiting for a free-slot number;
- `choose_employee` — waiting for an employee number when a name matches multiple employees;
- `choose_event` — waiting for an event number when reschedule/cancel matches multiple events;
- `await_reschedule_time` — waiting for the new time after the user selected which event to move.

This prevents a reply like `Да` in one Matrix room from confirming a pending action that was created in another room by the same employee.
