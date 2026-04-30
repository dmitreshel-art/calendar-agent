from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from .models import Meeting, MeetingParticipant, MeetingStatus, Employee
from .config import get_settings

WEEKDAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']


def as_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def busy_intervals(db: Session, employee: Employee, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    rows = db.execute(
        select(Meeting.start_time, Meeting.end_time)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .where(
            MeetingParticipant.employee_id == employee.id,
            Meeting.status == MeetingStatus.active.value,
            Meeting.end_time > start,
            Meeting.start_time < end,
        )
    ).all()
    return [(as_aware_utc(r[0]), as_aware_utc(r[1])) for r in rows]


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def is_free(interval_start: datetime, interval_end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return all(not overlaps(interval_start, interval_end, b_start, b_end) for b_start, b_end in busy)


def next_work_window(employee: Employee, day: datetime) -> tuple[datetime, datetime] | None:
    tz = ZoneInfo(employee.timezone)
    local_day = day.astimezone(tz)
    code = WEEKDAY_CODES[local_day.weekday()]
    if code not in employee.workdays:
        return None
    start_h, start_m = [int(x) for x in employee.workday_start.split(':')]
    end_h, end_m = [int(x) for x in employee.workday_end.split(':')]
    start = datetime.combine(local_day.date(), time(start_h, start_m), tzinfo=tz)
    end = datetime.combine(local_day.date(), time(end_h, end_m), tzinfo=tz)
    return start.astimezone(ZoneInfo('UTC')), end.astimezone(ZoneInfo('UTC'))


def default_search_range(requester: Employee) -> tuple[datetime, datetime]:
    settings = get_settings()
    tz = ZoneInfo(requester.timezone)
    now = datetime.now(tz).replace(second=0, microsecond=0)
    days = 0
    cursor = now
    last_end = now
    while days < settings.default_freebusy_workdays:
        window = next_work_window(requester, cursor)
        if window:
            days += 1
            last_end = window[1].astimezone(tz)
        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0)
    return now.astimezone(ZoneInfo('UTC')), last_end.astimezone(ZoneInfo('UTC'))


def find_free_slots(db: Session, participants: list[Employee], start: datetime, end: datetime, duration_minutes: int, max_slots: int) -> list[tuple[datetime, datetime]]:
    start = as_aware_utc(start)
    end = as_aware_utc(end)
    duration = timedelta(minutes=duration_minutes)
    busy_by_employee = {e.id: busy_intervals(db, e, start, end) for e in participants}
    slots: list[tuple[datetime, datetime]] = []
    cursor = start.replace(second=0, microsecond=0)
    if cursor.minute % 15:
        cursor += timedelta(minutes=15 - (cursor.minute % 15))
    while cursor + duration <= end and len(slots) < max_slots:
        candidate_end = cursor + duration
        in_worktime = True
        for e in participants:
            window = next_work_window(e, cursor)
            if not window or cursor < window[0] or candidate_end > window[1]:
                in_worktime = False
                break
        if in_worktime and all(is_free(cursor, candidate_end, busy_by_employee[e.id]) for e in participants):
            slots.append((cursor, candidate_end))
            cursor = candidate_end
        else:
            cursor += timedelta(minutes=15)
    return slots
