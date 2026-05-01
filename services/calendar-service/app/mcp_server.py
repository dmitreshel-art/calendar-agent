"""MCP wrapper for calendar_service.

Run locally:
    python -m app.mcp_server

The tools call the same business logic as REST handlers and use SQLite directly.
"""
import os
from datetime import datetime
from typing import Any
from fastmcp import FastMCP
from .database import init_db, session_scope
from sqlalchemy import select
from .schemas import ParticipantRef
from .employees import ensure_employee, find_employee
from .calendar_logic import (
    draft_create_event,
    draft_reschedule_event,
    draft_cancel_event,
    draft_clear_my_calendar,
    draft_admin_clear_calendar,
    confirm_pending_action,
    get_schedule,
    free_slots,
)
from .reminders import enqueue_due_reminders
from .audit import audit
from .models import AuditLog, Employee, OutboxMessage, PendingAction, utcnow

mcp = FastMCP('calendar-agent-service')


def _employee_dict(emp: Employee) -> dict[str, Any]:
    return {
        'id': emp.id,
        'matrix_id': emp.matrix_id,
        'matrix_server': emp.matrix_server,
        'localpart': emp.localpart,
        'display_name': emp.display_name,
        'email': emp.email,
        'calendar_path': emp.calendar_path,
        'timezone': emp.timezone,
        'workday_start': emp.workday_start,
        'workday_end': emp.workday_end,
        'workdays': emp.workdays,
        'status': emp.status,
        'role': emp.role,
    }


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
def draft_clear_my_calendar_tool(requester_matrix_id: str, start: str, end: str) -> dict[str, Any]:
    """Create a pending action to clear the requester's own calendar in a time range."""
    init_db()
    with session_scope() as db:
        action = draft_clear_my_calendar(db, requester_matrix_id, datetime.fromisoformat(start), datetime.fromisoformat(end))
        return {'id': action.id, 'requester_employee_id': action.requester_employee_id, 'action_type': action.action_type, 'payload': action.payload, 'status': action.status}


@mcp.tool
def admin_draft_clear_calendar_tool(admin_matrix_id: str, target_matrix_id: str, start: str, end: str, reason: str) -> dict[str, Any]:
    """Create a pending action for a calendar admin to clear another employee's calendar in a time range."""
    init_db()
    with session_scope() as db:
        action = draft_admin_clear_calendar(db, admin_matrix_id, target_matrix_id, datetime.fromisoformat(start), datetime.fromisoformat(end), reason)
        return {'id': action.id, 'requester_employee_id': action.requester_employee_id, 'action_type': action.action_type, 'payload': action.payload, 'status': action.status}


@mcp.tool
def list_pending_actions_tool(requester_matrix_id: str) -> list[dict[str, Any]]:
    """List pending calendar actions for a requester."""
    init_db()
    with session_scope() as db:
        emp = ensure_employee(db, requester_matrix_id)
        rows = list(db.scalars(select(PendingAction).where(PendingAction.requester_employee_id == emp.id, PendingAction.status == 'pending').order_by(PendingAction.created_at.desc())))
        return [{'id': a.id, 'requester_employee_id': a.requester_employee_id, 'action_type': a.action_type, 'payload': a.payload, 'status': a.status, 'created_at': a.created_at.isoformat(), 'expires_at': a.expires_at.isoformat() if a.expires_at else None} for a in rows]


@mcp.tool
def confirm_pending_action_tool(requester_matrix_id: str, pending_action_id: str, confirm: bool = True) -> dict[str, Any]:
    """Confirm or cancel a pending calendar action."""
    init_db()
    with session_scope() as db:
        status, message, meeting_id = confirm_pending_action(db, requester_matrix_id, pending_action_id, confirm)
        return {'status': status, 'message': message, 'meeting_id': meeting_id}


@mcp.tool
def admin_list_employees_tool() -> list[dict[str, Any]]:
    """List employees for calendar administration."""
    init_db()
    with session_scope() as db:
        rows = list(db.scalars(select(Employee).order_by(Employee.created_at.desc())))
        return [_employee_dict(e) for e in rows]


@mcp.tool
def admin_patch_employee_tool(employee_id: str, display_name: str | None = None, email: str | None = None, timezone: str | None = None, workday_start: str | None = None, workday_end: str | None = None, workdays: list[str] | None = None, role: str | None = None) -> dict[str, Any]:
    """Update employee settings such as timezone, workday, status metadata, or calendar role."""
    init_db()
    updates = {
        'display_name': display_name,
        'email': email,
        'timezone': timezone,
        'workday_start': workday_start,
        'workday_end': workday_end,
        'workdays': workdays,
        'role': role,
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    with session_scope() as db:
        emp = db.get(Employee, employee_id)
        if not emp:
            raise ValueError('Employee not found')
        for field, value in updates.items():
            setattr(emp, field, value)
        audit(db, 'admin_employee_updated', target_employee_id=employee_id, details=updates)
        db.flush()
        return _employee_dict(emp)


@mcp.tool
def admin_audit_log_tool(limit: int = 100) -> list[dict[str, Any]]:
    """Read recent calendar administration and mutation audit events."""
    init_db()
    with session_scope() as db:
        rows = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))
        return [{'id': r.id, 'event_type': r.event_type, 'actor_employee_id': r.actor_employee_id, 'target_employee_id': r.target_employee_id, 'details': r.details, 'created_at': r.created_at.isoformat()} for r in rows]


@mcp.tool
def deliver_notifications_tool() -> list[dict[str, Any]]:
    """Enqueue due reminders and return all pending outbox messages, marking them as delivered. Call periodically (e.g. every 5 minutes) to deliver calendar notifications."""
    init_db()
    from sqlalchemy import select
    from .models import utcnow
    with session_scope() as db:
        enqueue_due_reminders(db)
        db.flush()
        messages = list(db.scalars(select(OutboxMessage).where(OutboxMessage.delivered == False).order_by(OutboxMessage.created_at, OutboxMessage.id).limit(100)))
        result = [{'id': m.id, 'matrix_id': m.matrix_id, 'body': m.body} for m in messages]
        now = utcnow()
        for m in messages:
            m.delivered = True
            m.delivered_at = now
        db.commit()
        return result


if __name__ == '__main__':
    init_db()
    transport = os.environ.get('MCP_TRANSPORT', 'stdio')
    if transport == 'stdio':
        mcp.run()
    else:
        # HTTP / SSE / StreamableHTTP transport
        host = os.environ.get('MCP_HOST', '0.0.0.0')
        port = int(os.environ.get('MCP_PORT', '8765'))
        mcp.run(transport=transport, host=host, port=port)
