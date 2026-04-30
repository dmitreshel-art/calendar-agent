from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from .database import init_db, get_db
from .config import validate_runtime_settings
from .security import require_agent_token, require_admin_token
from .schemas import (
    EnsureEmployeeRequest, EmployeeOut, EmployeePatch, GetScheduleRequest, CalendarEventOut,
    FindFreeSlotsRequest, FreeSlotOut, DraftCreateEventRequest, DraftRescheduleEventRequest,
    DraftCancelEventRequest, ClearMyCalendarRequest, AdminClearCalendarRequest, PendingActionOut, ConfirmPendingActionRequest, ConfirmResult,
    SearchEmployeesRequest, ReminderOut, HealthOut, AgentMessageRequest, AgentMessageResponse,
)
from .employees import ensure_employee, find_employee
from .calendar_logic import (
    draft_create_event, draft_reschedule_event, draft_cancel_event, draft_clear_my_calendar,
    draft_admin_clear_calendar, confirm_pending_action, get_schedule, free_slots,
)
from .models import Employee, PendingAction, EmployeeStatus, AuditLog, OutboxMessage, utcnow
from .reminders import enqueue_due_reminders
from .audit import audit
from .agent import process_agent_message

VERSION = '0.2.1'
app = FastAPI(title='calendar-agent-service', version=VERSION)


@app.on_event('startup')
def on_startup() -> None:
    validate_runtime_settings()
    init_db()


@app.get('/health', response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status='ok', version=VERSION)


@app.get('/version')
def version() -> dict:
    return {'version': VERSION}


@app.post('/agent/message', response_model=AgentMessageResponse, dependencies=[Depends(require_agent_token)])
def api_agent_message(req: AgentMessageRequest, db: Session = Depends(get_db)):
    result = process_agent_message(db, req.matrix_id, req.message, req.display_name, req.email, req.conversation_id)
    db.commit()
    known = {'status', 'reply', 'pending_action_id', 'meeting_id'}
    data = {k: v for k, v in result.items() if k not in known}
    return AgentMessageResponse(
        status=result.get('status', 'ok'),
        reply=result.get('reply', ''),
        pending_action_id=result.get('pending_action_id'),
        meeting_id=result.get('meeting_id'),
        data=data or None,
    )


@app.post('/tools/ensure-employee', response_model=EmployeeOut, dependencies=[Depends(require_agent_token)])
def api_ensure_employee(req: EnsureEmployeeRequest, db: Session = Depends(get_db)):
    emp = ensure_employee(db, req.matrix_id, req.display_name, req.email)
    db.commit()
    db.refresh(emp)
    return emp


@app.post('/tools/search-employees', response_model=list[EmployeeOut], dependencies=[Depends(require_agent_token)])
def api_search_employees(req: SearchEmployeesRequest, db: Session = Depends(get_db)):
    ensure_employee(db, req.requester_matrix_id)
    return find_employee(db, req.query)[:req.limit]


@app.post('/tools/get-schedule', response_model=list[CalendarEventOut], dependencies=[Depends(require_agent_token)])
def api_get_schedule(req: GetScheduleRequest, db: Session = Depends(get_db)):
    return get_schedule(db, req.requester_matrix_id, req.target_matrix_id, req.start, req.end)


@app.post('/tools/find-free-slots', response_model=list[FreeSlotOut], dependencies=[Depends(require_agent_token)])
def api_find_free_slots(req: FindFreeSlotsRequest, db: Session = Depends(get_db)):
    return free_slots(db, req.requester_matrix_id, req.participants, req.duration_minutes, req.start, req.end, req.max_slots)


@app.post('/tools/draft-create-event', response_model=PendingActionOut, dependencies=[Depends(require_agent_token)])
def api_draft_create_event(req: DraftCreateEventRequest, db: Session = Depends(get_db)):
    action = draft_create_event(db, req.requester_matrix_id, req.title, req.description, req.participants, req.start, req.end, req.timezone, req.reminder_minutes, req.no_reminder)
    db.commit()
    db.refresh(action)
    return action


@app.post('/tools/draft-reschedule-event', response_model=PendingActionOut, dependencies=[Depends(require_agent_token)])
def api_draft_reschedule_event(req: DraftRescheduleEventRequest, db: Session = Depends(get_db)):
    action = draft_reschedule_event(db, req.requester_matrix_id, req.meeting_id, req.new_start, req.new_end)
    db.commit()
    db.refresh(action)
    return action


@app.post('/tools/draft-cancel-event', response_model=PendingActionOut, dependencies=[Depends(require_agent_token)])
def api_draft_cancel_event(req: DraftCancelEventRequest, db: Session = Depends(get_db)):
    action = draft_cancel_event(db, req.requester_matrix_id, req.meeting_id)
    db.commit()
    db.refresh(action)
    return action


@app.post('/tools/draft-clear-my-calendar', response_model=PendingActionOut, dependencies=[Depends(require_agent_token)])
def api_draft_clear_my_calendar(req: ClearMyCalendarRequest, db: Session = Depends(get_db)):
    action = draft_clear_my_calendar(db, req.requester_matrix_id, req.start, req.end)
    db.commit()
    db.refresh(action)
    return action


@app.post('/tools/confirm-pending-action', response_model=ConfirmResult, dependencies=[Depends(require_agent_token)])
def api_confirm_pending_action(req: ConfirmPendingActionRequest, db: Session = Depends(get_db)):
    status, message, meeting_id = confirm_pending_action(db, req.requester_matrix_id, req.pending_action_id, req.confirm)
    db.commit()
    return ConfirmResult(status=status, message=message, meeting_id=meeting_id)


@app.get('/tools/pending-actions', response_model=list[PendingActionOut], dependencies=[Depends(require_agent_token)])
def api_pending_actions(requester_matrix_id: str, db: Session = Depends(get_db)):
    emp = ensure_employee(db, requester_matrix_id)
    return list(db.scalars(select(PendingAction).where(PendingAction.requester_employee_id == emp.id, PendingAction.status == 'pending').order_by(PendingAction.created_at.desc())))


@app.post('/tools/reminders/enqueue-due', response_model=list[ReminderOut], dependencies=[Depends(require_agent_token)])
def api_enqueue_due_reminders(db: Session = Depends(get_db)):
    messages = enqueue_due_reminders(db)
    db.flush()
    return [ReminderOut(id=m.id, matrix_id=m.matrix_id, body=m.body) for m in messages]


@app.get('/tools/outbox', response_model=list[ReminderOut], dependencies=[Depends(require_agent_token)])
def api_outbox(limit: int = 100, db: Session = Depends(get_db)):
    messages = list(db.scalars(select(OutboxMessage).where(OutboxMessage.delivered == False).order_by(OutboxMessage.created_at, OutboxMessage.id).limit(limit)))  # noqa: E712
    return [ReminderOut(id=m.id, matrix_id=m.matrix_id, body=m.body) for m in messages]


@app.post('/tools/outbox/{message_id}/mark-delivered', dependencies=[Depends(require_agent_token)])
def api_mark_delivered(message_id: int, db: Session = Depends(get_db)):
    msg = db.get(OutboxMessage, message_id)
    if not msg:
        raise HTTPException(status_code=404, detail='Outbox message not found')
    msg.delivered = True
    msg.delivered_at = utcnow()
    db.commit()
    return {'status': 'ok'}


@app.get('/admin/employees', response_model=list[EmployeeOut], dependencies=[Depends(require_admin_token)])
def admin_employees(db: Session = Depends(get_db)):
    return list(db.scalars(select(Employee).order_by(Employee.created_at.desc())))


@app.get('/admin/employees/{employee_id}', response_model=EmployeeOut, dependencies=[Depends(require_admin_token)])
def admin_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail='Employee not found')
    return emp


@app.patch('/admin/employees/{employee_id}', response_model=EmployeeOut, dependencies=[Depends(require_admin_token)])
def admin_patch_employee(employee_id: str, req: EmployeePatch, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail='Employee not found')
    for field, value in req.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)
    audit(db, 'admin_employee_updated', target_employee_id=employee_id, details=req.model_dump(exclude_unset=True))
    db.commit()
    db.refresh(emp)
    return emp


@app.post('/admin/draft-clear-calendar', response_model=PendingActionOut, dependencies=[Depends(require_admin_token)])
def admin_draft_clear_calendar(req: AdminClearCalendarRequest, db: Session = Depends(get_db)):
    action = draft_admin_clear_calendar(db, req.admin_matrix_id, req.target_matrix_id, req.start, req.end, req.reason)
    db.commit()
    db.refresh(action)
    return action


@app.post('/admin/employees/{employee_id}/block', dependencies=[Depends(require_admin_token)])
def admin_block_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail='Employee not found')
    emp.status = EmployeeStatus.blocked.value
    audit(db, 'employee_blocked', target_employee_id=employee_id)
    db.commit()
    return {'status': 'blocked'}


@app.post('/admin/employees/{employee_id}/unblock', dependencies=[Depends(require_admin_token)])
def admin_unblock_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail='Employee not found')
    emp.status = EmployeeStatus.active.value
    audit(db, 'employee_unblocked', target_employee_id=employee_id)
    db.commit()
    return {'status': 'active'}


@app.get('/admin/audit-log', dependencies=[Depends(require_admin_token)])
def admin_audit_log(limit: int = 100, db: Session = Depends(get_db)):
    rows = list(db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)))
    return [{'id': r.id, 'event_type': r.event_type, 'actor_employee_id': r.actor_employee_id, 'target_employee_id': r.target_employee_id, 'details': r.details, 'created_at': r.created_at} for r in rows]
