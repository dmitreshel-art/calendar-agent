from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from fastapi import HTTPException
from .models import Meeting, MeetingParticipant, EventCopy, PendingAction, PendingActionStatus, MeetingStatus, Employee, Reminder, utcnow
from .employees import ensure_employee, resolve_participant
from .radicale_client import RadicaleClient, new_uid
from .freebusy import default_search_range, find_free_slots as find_slots
from .reminders import schedule_meeting_reminders
from .audit import audit
from .config import get_settings
from .schemas import ParticipantRef
from .policy import can_admin_clear_calendar, can_modify_meeting, can_view_details


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _maybe_aware_utc(dt: datetime | None) -> datetime | None:
    return _aware_utc(dt) if dt is not None else None


def _ensure_future_start(start_u: datetime, field_name: str = 'start') -> None:
    now = _aware_utc(utcnow())
    if start_u <= now:
        raise HTTPException(
            status_code=400,
            detail={
                'error': 'event_start_must_be_in_future',
                'message': f'{field_name} must be in the future',
                field_name: start_u.isoformat(),
                'now': now.isoformat(),
            },
        )


def resolve_participants(db: Session, requester: Employee, refs: list[ParticipantRef]) -> list[Employee]:
    participants = {requester.id: requester}
    for ref in refs:
        emp = resolve_participant(db, matrix_id=ref.matrix_id, query=ref.query)
        participants[emp.id] = emp
    return list(participants.values())


def generate_title(requester: Employee, participants: list[Employee], explicit_title: str | None) -> str:
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    others = [p for p in participants if p.id != requester.id]
    if not others:
        return 'Личное событие'
    names = [p.display_name or p.localpart for p in others]
    if len(names) == 1:
        return f'Встреча с {names[0]}'
    return 'Встреча с ' + ', '.join(names[:2]) + (f' и еще {len(names)-2}' if len(names) > 2 else '')


def create_pending_action(db: Session, requester: Employee, action_type: str, payload: dict) -> PendingAction:
    action = PendingAction(
        id=uuid4().hex,
        requester_employee_id=requester.id,
        action_type=action_type,
        payload=payload,
        status=PendingActionStatus.pending.value,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    db.add(action)
    db.flush()
    audit(db, 'event_draft_created', actor_employee_id=requester.id, details={'action_id': action.id, 'action_type': action_type})
    return action


def draft_create_event(db: Session, requester_matrix_id: str, title: str | None, description: str | None, refs: list[ParticipantRef], start: datetime, end: datetime, timezone_name: str | None, reminder_minutes: int | None, no_reminder: bool) -> PendingAction:
    requester = ensure_employee(db, requester_matrix_id)
    participants = resolve_participants(db, requester, refs)
    start_u = _aware_utc(start)
    end_u = _aware_utc(end)
    if end_u <= start_u:
        raise HTTPException(status_code=400, detail='end must be after start')
    _ensure_future_start(start_u)
    settings = get_settings()
    reminder = None if no_reminder else (reminder_minutes if reminder_minutes is not None else settings.default_reminder_minutes)
    payload = {
        'title': generate_title(requester, participants, title),
        'description': description,
        'participant_employee_ids': [p.id for p in participants],
        'start': start_u.isoformat(),
        'end': end_u.isoformat(),
        'timezone': timezone_name or requester.timezone,
        'reminder_minutes': reminder,
    }
    return create_pending_action(db, requester, 'create_event', payload)


def _materialize_create_event(db: Session, action: PendingAction) -> Meeting:
    payload = action.payload
    requester = db.get(Employee, action.requester_employee_id)
    participants = [db.get(Employee, eid) for eid in payload['participant_employee_ids']]
    participants = [p for p in participants if p]
    if not requester or not participants:
        raise HTTPException(status_code=400, detail='Invalid pending action participants')
    start_time = datetime.fromisoformat(payload['start'])
    end_time = datetime.fromisoformat(payload['end'])
    _ensure_future_start(_aware_utc(start_time))
    meeting = Meeting(
        title=payload['title'],
        description=payload.get('description'),
        start_time=start_time,
        end_time=end_time,
        timezone=payload.get('timezone') or requester.timezone,
        organizer_employee_id=requester.id,
        reminder_minutes=payload.get('reminder_minutes'),
    )
    db.add(meeting)
    db.flush()
    radicale = RadicaleClient()
    for p in participants:
        db.add(MeetingParticipant(meeting_id=meeting.id, employee_id=p.id))
        uid = new_uid()
        radicale.ensure_calendar(p.calendar_path, p.display_name)
        radicale.create_or_update_event(p.calendar_path, uid, meeting.title, meeting.start_time, meeting.end_time, meeting.timezone, meeting.description)
        db.add(EventCopy(meeting_id=meeting.id, employee_id=p.id, calendar_path=p.calendar_path, event_uid=uid))
    schedule_meeting_reminders(db, meeting, participants)
    audit(db, 'event_created', actor_employee_id=requester.id, details={'meeting_id': meeting.id})
    return meeting


def draft_reschedule_event(db: Session, requester_matrix_id: str, meeting_id: int, new_start: datetime, new_end: datetime) -> PendingAction:
    requester = ensure_employee(db, requester_matrix_id)
    meeting = db.get(Meeting, meeting_id)
    if not meeting or meeting.status != MeetingStatus.active.value:
        raise HTTPException(status_code=404, detail='Meeting not found')
    if not can_modify_meeting(requester, meeting):
        # MVP: only organizer can reschedule through user tools.
        raise HTTPException(status_code=403, detail='Only organizer can reschedule the meeting in MVP')
    new_start_u = _aware_utc(new_start)
    new_end_u = _aware_utc(new_end)
    if new_end_u <= new_start_u:
        raise HTTPException(status_code=400, detail='new_end must be after new_start')
    _ensure_future_start(new_start_u, 'new_start')
    payload = {'meeting_id': meeting_id, 'new_start': new_start_u.isoformat(), 'new_end': new_end_u.isoformat()}
    return create_pending_action(db, requester, 'reschedule_event', payload)


def _materialize_reschedule(db: Session, action: PendingAction) -> Meeting:
    meeting = db.get(Meeting, action.payload['meeting_id'])
    if not meeting:
        raise HTTPException(status_code=404, detail='Meeting not found')
    meeting.start_time = datetime.fromisoformat(action.payload['new_start'])
    meeting.end_time = datetime.fromisoformat(action.payload['new_end'])
    if meeting.end_time <= meeting.start_time:
        raise HTTPException(status_code=400, detail='new_end must be after new_start')
    _ensure_future_start(_aware_utc(meeting.start_time), 'new_start')
    db.execute(delete(Reminder).where(Reminder.meeting_id == meeting.id, Reminder.sent == False))  # noqa: E712
    participants = db.scalars(select(Employee).join(MeetingParticipant, MeetingParticipant.employee_id == Employee.id).where(MeetingParticipant.meeting_id == meeting.id)).all()
    schedule_meeting_reminders(db, meeting, list(participants))
    radicale = RadicaleClient()
    for copy in meeting.event_copies:
        radicale.create_or_update_event(copy.calendar_path, copy.event_uid, meeting.title, meeting.start_time, meeting.end_time, meeting.timezone, meeting.description)
    audit(db, 'event_rescheduled', actor_employee_id=action.requester_employee_id, details={'meeting_id': meeting.id})
    return meeting


def draft_cancel_event(db: Session, requester_matrix_id: str, meeting_id: int) -> PendingAction:
    requester = ensure_employee(db, requester_matrix_id)
    meeting = db.get(Meeting, meeting_id)
    if not meeting or meeting.status != MeetingStatus.active.value:
        raise HTTPException(status_code=404, detail='Meeting not found')
    if not can_modify_meeting(requester, meeting):
        raise HTTPException(status_code=403, detail='Only organizer can cancel the meeting in MVP')
    return create_pending_action(db, requester, 'cancel_event', {'meeting_id': meeting_id})


def _materialize_cancel(db: Session, action: PendingAction) -> Meeting:
    meeting = db.get(Meeting, action.payload['meeting_id'])
    if not meeting:
        raise HTTPException(status_code=404, detail='Meeting not found')
    meeting.status = MeetingStatus.cancelled.value
    db.execute(delete(Reminder).where(Reminder.meeting_id == meeting.id, Reminder.sent == False))  # noqa: E712
    radicale = RadicaleClient()
    for copy in meeting.event_copies:
        radicale.delete_event(copy.calendar_path, copy.event_uid)
    audit(db, 'event_cancelled', actor_employee_id=action.requester_employee_id, details={'meeting_id': meeting.id})
    return meeting


def _meeting_ids_for_employee_range(db: Session, employee: Employee, start: datetime, end: datetime) -> list[int]:
    return list(db.scalars(
        select(Meeting.id)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .where(
            MeetingParticipant.employee_id == employee.id,
            Meeting.status == MeetingStatus.active.value,
            Meeting.end_time > _aware_utc(start),
            Meeting.start_time < _aware_utc(end),
        )
        .order_by(Meeting.start_time)
    ))


def draft_clear_my_calendar(db: Session, requester_matrix_id: str, start: datetime, end: datetime) -> PendingAction:
    requester = ensure_employee(db, requester_matrix_id)
    start_u = _aware_utc(start)
    end_u = _aware_utc(end)
    if end_u <= start_u:
        raise HTTPException(status_code=400, detail='end must be after start')
    payload = {
        'target_employee_id': requester.id,
        'target_matrix_id': requester.matrix_id,
        'start': start_u.isoformat(),
        'end': end_u.isoformat(),
        'meeting_ids': _meeting_ids_for_employee_range(db, requester, start_u, end_u),
        'mode': 'self',
    }
    return create_pending_action(db, requester, 'clear_calendar', payload)


def draft_admin_clear_calendar(db: Session, admin_matrix_id: str, target_matrix_id: str, start: datetime, end: datetime, reason: str) -> PendingAction:
    admin = ensure_employee(db, admin_matrix_id)
    target = ensure_employee(db, target_matrix_id)
    if not can_admin_clear_calendar(admin, target):
        raise HTTPException(status_code=403, detail='Only calendar admin can clear another calendar')
    start_u = _aware_utc(start)
    end_u = _aware_utc(end)
    if end_u <= start_u:
        raise HTTPException(status_code=400, detail='end must be after start')
    reason = reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail='reason is required')
    payload = {
        'target_employee_id': target.id,
        'target_matrix_id': target.matrix_id,
        'start': start_u.isoformat(),
        'end': end_u.isoformat(),
        'meeting_ids': _meeting_ids_for_employee_range(db, target, start_u, end_u),
        'mode': 'admin',
        'reason': reason,
    }
    return create_pending_action(db, admin, 'clear_calendar', payload)


def _materialize_clear_calendar(db: Session, action: PendingAction) -> list[int]:
    payload = action.payload
    meeting_ids = list(payload.get('meeting_ids') or [])
    deleted: list[int] = []
    radicale = RadicaleClient()
    for meeting_id in meeting_ids:
        meeting = db.get(Meeting, meeting_id)
        if not meeting or meeting.status != MeetingStatus.active.value:
            continue
        meeting.status = MeetingStatus.cancelled.value
        db.execute(delete(Reminder).where(Reminder.meeting_id == meeting.id, Reminder.sent == False))  # noqa: E712
        for copy in meeting.event_copies:
            radicale.delete_event(copy.calendar_path, copy.event_uid)
        deleted.append(meeting.id)
    audit(
        db,
        'calendar_cleared',
        actor_employee_id=action.requester_employee_id,
        target_employee_id=payload.get('target_employee_id'),
        details={
            'mode': payload.get('mode'),
            'start': payload.get('start'),
            'end': payload.get('end'),
            'reason': payload.get('reason'),
            'deleted_meeting_ids': deleted,
        },
    )
    return deleted


def confirm_pending_action(db: Session, requester_matrix_id: str, pending_action_id: str, confirm: bool) -> tuple[str, str, int | None]:
    requester = ensure_employee(db, requester_matrix_id)
    action = db.get(PendingAction, pending_action_id)
    if not action or action.requester_employee_id != requester.id:
        raise HTTPException(status_code=404, detail='Pending action not found')
    if action.status != PendingActionStatus.pending.value:
        raise HTTPException(status_code=409, detail=f'Pending action is {action.status}')
    if not confirm:
        action.status = PendingActionStatus.cancelled.value
        return 'cancelled', 'Действие отменено.', None
    expires_at = _maybe_aware_utc(action.expires_at)
    if expires_at and expires_at < datetime.now(timezone.utc):
        action.status = PendingActionStatus.expired.value
        raise HTTPException(status_code=409, detail='Pending action expired')
    meeting_id = None
    if action.action_type == 'create_event':
        meeting = _materialize_create_event(db, action)
        meeting_id = meeting.id
        message = f'Событие создано: {meeting.title}'
    elif action.action_type == 'reschedule_event':
        meeting = _materialize_reschedule(db, action)
        meeting_id = meeting.id
        message = f'Событие перенесено: {meeting.title}'
    elif action.action_type == 'cancel_event':
        meeting = _materialize_cancel(db, action)
        meeting_id = meeting.id
        message = f'Событие отменено: {meeting.title}'
    elif action.action_type == 'clear_calendar':
        deleted = _materialize_clear_calendar(db, action)
        message = f'Календарь очищен. Удалено событий: {len(deleted)}'
    else:
        raise HTTPException(status_code=400, detail='Unsupported action type')
    action.status = PendingActionStatus.confirmed.value
    action.confirmed_at = datetime.now(timezone.utc)
    return 'confirmed', message, meeting_id


def get_schedule(db: Session, requester_matrix_id: str, target_matrix_id: str | None, start: datetime, end: datetime) -> list[dict]:
    requester = ensure_employee(db, requester_matrix_id)
    target = ensure_employee(db, target_matrix_id) if target_matrix_id else requester
    rows = db.scalars(
        select(Meeting)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .where(MeetingParticipant.employee_id == target.id, Meeting.status == MeetingStatus.active.value, Meeting.end_time > _aware_utc(start), Meeting.start_time < _aware_utc(end))
        .order_by(Meeting.start_time)
    ).all()
    result = []
    for m in rows:
        if not can_view_details(requester, target):
            result.append({'meeting_id': None, 'title': 'Занят', 'description': None, 'start': _aware_utc(m.start_time), 'end': _aware_utc(m.end_time), 'timezone': target.timezone, 'participants': []})
        else:
            participants = db.scalars(select(Employee).join(MeetingParticipant, MeetingParticipant.employee_id == Employee.id).where(MeetingParticipant.meeting_id == m.id)).all()
            result.append({'meeting_id': m.id, 'title': m.title, 'description': m.description, 'start': _aware_utc(m.start_time), 'end': _aware_utc(m.end_time), 'timezone': m.timezone, 'participants': [p.matrix_id for p in participants]})
    return result


def free_slots(db: Session, requester_matrix_id: str, refs: list[ParticipantRef], duration_minutes: int, start: datetime | None, end: datetime | None, max_slots: int | None) -> list[dict]:
    requester = ensure_employee(db, requester_matrix_id)
    participants = resolve_participants(db, requester, refs)
    if start is None or end is None:
        start, end = default_search_range(requester)
    settings = get_settings()
    slots = find_slots(db, participants, _aware_utc(start), _aware_utc(end), duration_minutes, max_slots or settings.max_free_slots)
    return [{'start': s, 'end': e, 'participants': [p.matrix_id for p in participants]} for s, e in slots]
