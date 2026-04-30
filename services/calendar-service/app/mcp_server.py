"""MCP wrapper for calendar_service.

Run locally:
    python -m app.mcp_server

The tools call the same business logic as REST handlers and use SQLite directly.
"""
from datetime import datetime
from typing import Any
from fastmcp import FastMCP
from .database import init_db, session_scope
from .schemas import ParticipantRef
from .employees import ensure_employee, find_employee
from .calendar_logic import draft_create_event, draft_reschedule_event, draft_cancel_event, confirm_pending_action, get_schedule, free_slots
from .agent import process_agent_message

mcp = FastMCP('calendar-agent-service')


@mcp.tool
def agent_message_tool(matrix_id: str, message: str, display_name: str | None = None, email: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
    """Process a natural-language Matrix message through the LLM calendar agent runtime."""
    init_db()
    with session_scope() as db:
        return dict(process_agent_message(db, matrix_id, message, display_name, email, conversation_id))


@mcp.tool
def ensure_employee_tool(matrix_id: str, display_name: str | None = None, email: str | None = None) -> dict[str, Any]:
    """Ensure employee exists by Matrix ID and create calendar mapping if needed."""
    init_db()
    with session_scope() as db:
        emp = ensure_employee(db, matrix_id, display_name, email)
        return {'id': emp.id, 'matrix_id': emp.matrix_id, 'display_name': emp.display_name, 'calendar_path': emp.calendar_path, 'timezone': emp.timezone}


@mcp.tool
def search_employees_tool(requester_matrix_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search employees by Matrix ID, display name or localpart."""
    init_db()
    with session_scope() as db:
        ensure_employee(db, requester_matrix_id)
        return [{'id': e.id, 'matrix_id': e.matrix_id, 'display_name': e.display_name, 'matrix_server': e.matrix_server} for e in find_employee(db, query)[:limit]]


@mcp.tool
def get_schedule_tool(requester_matrix_id: str, start: str, end: str, target_matrix_id: str | None = None) -> list[dict[str, Any]]:
    """Get own schedule. For another employee returns free/busy only."""
    init_db()
    with session_scope() as db:
        rows = get_schedule(db, requester_matrix_id, target_matrix_id, datetime.fromisoformat(start), datetime.fromisoformat(end))
        return [{**r, 'start': r['start'].isoformat(), 'end': r['end'].isoformat()} for r in rows]


@mcp.tool
def find_free_slots_tool(requester_matrix_id: str, participants: list[dict[str, str]], duration_minutes: int = 30, start: str | None = None, end: str | None = None, max_slots: int | None = None) -> list[dict[str, Any]]:
    """Find up to N free slots for requester and participants."""
    init_db()
    refs = [ParticipantRef(**p) for p in participants]
    with session_scope() as db:
        slots = free_slots(db, requester_matrix_id, refs, duration_minutes, datetime.fromisoformat(start) if start else None, datetime.fromisoformat(end) if end else None, max_slots)
        return [{**s, 'start': s['start'].isoformat(), 'end': s['end'].isoformat()} for s in slots]


@mcp.tool
def draft_create_event_tool(requester_matrix_id: str, start: str, end: str, title: str | None = None, description: str | None = None, participants: list[dict[str, str]] | None = None, timezone: str | None = None, reminder_minutes: int | None = None, no_reminder: bool = False) -> dict[str, Any]:
    """Create a pending action for an event. User confirmation is required."""
    init_db()
    refs = [ParticipantRef(**p) for p in (participants or [])]
    with session_scope() as db:
        action = draft_create_event(db, requester_matrix_id, title, description, refs, datetime.fromisoformat(start), datetime.fromisoformat(end), timezone, reminder_minutes, no_reminder)
        return {'id': action.id, 'action_type': action.action_type, 'payload': action.payload, 'status': action.status}


@mcp.tool
def draft_reschedule_event_tool(requester_matrix_id: str, meeting_id: int, new_start: str, new_end: str) -> dict[str, Any]:
    """Create a pending action to reschedule an existing meeting."""
    init_db()
    with session_scope() as db:
        action = draft_reschedule_event(db, requester_matrix_id, meeting_id, datetime.fromisoformat(new_start), datetime.fromisoformat(new_end))
        return {'id': action.id, 'action_type': action.action_type, 'payload': action.payload, 'status': action.status}


@mcp.tool
def draft_cancel_event_tool(requester_matrix_id: str, meeting_id: int) -> dict[str, Any]:
    """Create a pending action to cancel an existing meeting."""
    init_db()
    with session_scope() as db:
        action = draft_cancel_event(db, requester_matrix_id, meeting_id)
        return {'id': action.id, 'action_type': action.action_type, 'payload': action.payload, 'status': action.status}


@mcp.tool
def confirm_pending_action_tool(requester_matrix_id: str, pending_action_id: str, confirm: bool = True) -> dict[str, Any]:
    """Confirm or cancel a pending calendar action."""
    init_db()
    with session_scope() as db:
        status, message, meeting_id = confirm_pending_action(db, requester_matrix_id, pending_action_id, confirm)
        return {'status': status, 'message': message, 'meeting_id': meeting_id}


if __name__ == '__main__':
    init_db()
    mcp.run()
