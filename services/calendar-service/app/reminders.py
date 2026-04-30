from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import select
from .models import Meeting, MeetingParticipant, Employee, Reminder, OutboxMessage, utcnow
from .audit import audit


def reminder_text(meeting: Meeting, participants: list[Employee]) -> str:
    names = [p.display_name or p.matrix_id for p in participants]
    try:
        tz = ZoneInfo(meeting.timezone)
    except Exception:
        tz = timezone.utc
    start = meeting.start_time.replace(tzinfo=timezone.utc) if meeting.start_time.tzinfo is None else meeting.start_time
    end = meeting.end_time.replace(tzinfo=timezone.utc) if meeting.end_time.tzinfo is None else meeting.end_time
    start_s = start.astimezone(tz).strftime('%Y-%m-%d %H:%M')
    end_s = end.astimezone(tz).strftime('%H:%M')
    return (
        f'Напоминание: скоро событие\n'
        f'«{meeting.title}»\n'
        f'Время: {start_s} — {end_s} ({meeting.timezone})\n'
        f'Участники: {", ".join(names)}'
    )


def schedule_meeting_reminders(db: Session, meeting: Meeting, participants: list[Employee]) -> None:
    if meeting.reminder_minutes is None:
        return
    remind_at = meeting.start_time - timedelta(minutes=meeting.reminder_minutes)
    body = reminder_text(meeting, participants)
    for p in participants:
        db.add(Reminder(meeting_id=meeting.id, employee_id=p.id, remind_at=remind_at, message=body))


def enqueue_due_reminders(db: Session, limit: int = 100) -> list[OutboxMessage]:
    now = utcnow()
    due = list(db.scalars(select(Reminder).where(Reminder.sent == False, Reminder.remind_at <= now).limit(limit)))  # noqa: E712
    out: list[OutboxMessage] = []
    for r in due:
        emp = db.get(Employee, r.employee_id)
        if not emp:
            continue
        msg = OutboxMessage(matrix_id=emp.matrix_id, body=r.message)
        db.add(msg)
        r.sent = True
        r.sent_at = now
        audit(db, 'reminder_sent', target_employee_id=emp.id, details={'meeting_id': r.meeting_id})
        out.append(msg)
    return out
