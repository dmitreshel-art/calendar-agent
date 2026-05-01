# Calendar Agent Backend — Specification

## Purpose

Calendar Agent is a backend and MCP/REST tool server for a Matrix/Hermes calendar assistant. Employees interact with Hermes. Hermes performs natural-language understanding and calls structured Calendar Agent tools. Calendar Agent stores calendars in Radicale/CalDAV and enforces business logic, permissions, confirmation, reminders, and audit.

## Architecture

```text
Matrix / Element / Telegram
  ↓
Hermes Agent
  ↓ MCP tools or REST /tools/* and /admin/*
calendar_service
  ↓ CalDAV
Radicale
```

`calendar_service` owns business logic. Radicale is storage only. Hermes is the only conversational/LLM agent in the architecture.

## MVP scope

Implemented:

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
- role-based calendar permissions;
- admin REST API;
- audit log;
- REST API;
- MCP tools;
- demo seed.

Out of scope:

- embedded natural-language agent runtime inside calendar_service;
- separate LLM configuration inside calendar_service;
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

## Employee settings and roles

Each employee has:

- timezone;
- workday start;
- workday end;
- workdays;
- status;
- role.

Roles:

- `user`;
- `calendar_admin`.

Defaults come from `.env` and can be changed via admin API or admin MCP tools.

## Meeting model

A logical meeting is stored in SQLite and materialized as copies in Radicale:

```text
Meeting
  ├─ EventCopy for participant A
  ├─ EventCopy for participant B
  └─ EventCopy for participant C
```

This avoids reliance on email invites and calendar client RSVP behavior.

## Permission model

- Users can view full details for their own calendar.
- Users can view only free/busy for other employees.
- Only the meeting organizer can reschedule/cancel a meeting through user tools.
- Users can clear their own calendar for a selected range.
- `calendar_admin` can draft clearing another employee calendar for a selected range.
- Calendar mutations are recorded in audit log.

## Confirmation model

All calendar changes create `pending_actions` first. No Radicale change is performed until confirmation.

Pending action types:

- `create_event`;
- `reschedule_event`;
- `cancel_event`;
- `clear_calendar`.

Expected Hermes flow:

1. Parse user request in Hermes.
2. Call the relevant `draft_*` MCP tool or REST endpoint.
3. Present returned pending action payload to the user.
4. On explicit confirmation, call `confirm_pending_action_tool` or `/tools/confirm-pending-action`.
5. Calendar Agent applies the mutation and records audit.

## Free/busy privacy

When requesting another employee's schedule, only free/busy information is exposed. Event titles, descriptions, and participants are hidden.

## Free slot search defaults

When no search period is supplied, search over the next five working days. Return up to three slots by default.

## Reminders

Because employees use Matrix as their main interface, reminders are not only CalDAV alarms. The service creates reminder records and an outbox of Matrix messages.

A Matrix/Hermes worker should:

1. call `deliver_notifications_tool` via MCP, or REST `/tools/reminders/enqueue-due` + `/tools/outbox`;
2. send each body to the given `matrix_id`;
3. mark REST-delivered messages as delivered when using REST outbox endpoints.

Supported reminder model:

- default reminder: 15 minutes;
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

- `employee_auto_provisioned`;
- `calendar_created`;
- `event_draft_created`;
- `event_created`;
- `event_rescheduled`;
- `event_cancelled`;
- `calendar_cleared`;
- `reminder_sent`;
- `employee_blocked`;
- `employee_unblocked`;
- `admin_employee_updated`.

Full user messages are not stored in Calendar Agent audit; Hermes owns conversation history.

## REST API

Tool endpoints:

- `POST /tools/ensure-employee`
- `POST /tools/search-employees`
- `POST /tools/get-schedule`
- `POST /tools/find-free-slots`
- `POST /tools/draft-create-event`
- `POST /tools/draft-reschedule-event`
- `POST /tools/draft-cancel-event`
- `POST /tools/draft-clear-my-calendar`
- `POST /tools/confirm-pending-action`
- `GET /tools/pending-actions`
- `POST /tools/reminders/enqueue-due`
- `GET /tools/outbox`
- `POST /tools/outbox/{message_id}/mark-delivered`

Admin endpoints:

- `GET /admin/employees`
- `GET /admin/employees/{id}`
- `PATCH /admin/employees/{id}`
- `POST /admin/draft-clear-calendar`
- `POST /admin/employees/{id}/block`
- `POST /admin/employees/{id}/unblock`
- `GET /admin/audit-log`

## MCP tools

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

## Notes on removed legacy mode

Earlier MVP builds exposed an embedded `/agent/message` natural-language endpoint and an MCP `agent_message_tool`. That mode has been removed to avoid a nested `LLM → tool → LLM → business logic` architecture. Hermes now owns NLU/dialogue; Calendar Agent only exposes deterministic tools.
