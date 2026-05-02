from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.employees import ensure_employee
from app.models import AuditLog, Employee, EmployeeRole, Meeting, MeetingParticipant, MeetingStatus
from app.policy import can_admin_clear_calendar, can_clear_own_calendar, can_modify_meeting, can_view_details
from app.calendar_logic import (
    confirm_pending_action,
    draft_admin_clear_calendar,
    draft_clear_my_calendar,
    draft_create_event,
    draft_cancel_event,
    draft_reschedule_event,
    get_schedule,
)


def test_employee_role_values_are_stable():
    assert EmployeeRole.user.value == 'user'
    assert EmployeeRole.calendar_admin.value == 'calendar_admin'


def emp(employee_id, role='user'):
    return SimpleNamespace(id=employee_id, role=role)


def meeting(organizer_id='u1'):
    return SimpleNamespace(organizer_employee_id=organizer_id)


def test_policy_helpers_keep_calendar_permissions_deterministic():
    assert can_view_details(emp('u1'), emp('u1')) is True
    assert can_view_details(emp('u1'), emp('u2')) is False
    assert can_modify_meeting(emp('u1'), meeting('u1')) is True
    assert can_modify_meeting(emp('u2'), meeting('u1')) is False
    assert can_modify_meeting(emp('admin', 'calendar_admin'), meeting('u1')) is False
    assert can_clear_own_calendar(emp('u1'), emp('u1')) is True
    assert can_clear_own_calendar(emp('u1'), emp('u2')) is False
    assert can_admin_clear_calendar(emp('admin', 'calendar_admin'), emp('u2')) is True
    assert can_admin_clear_calendar(emp('admin', 'user'), emp('u2')) is False


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()

    monkeypatch.setattr('app.employees.RadicaleClient', lambda: SimpleNamespace(ensure_calendar=lambda *a, **k: None))
    monkeypatch.setattr('app.calendar_logic.RadicaleClient', lambda: SimpleNamespace(
        ensure_calendar=lambda *a, **k: None,
        create_or_update_event=lambda *a, **k: None,
        delete_event=lambda *a, **k: None,
    ))
    try:
        yield session
    finally:
        session.close()


def make_employee(db, matrix_id, display_name=None, role='user'):
    employee = ensure_employee(db, matrix_id, display_name or matrix_id)
    employee.role = role
    db.flush()
    return employee


def make_meeting(db, organizer, participants, title='Test meeting'):
    meeting = Meeting(
        title=title,
        description=None,
        start_time=datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 5, 5, 7, 0, tzinfo=timezone.utc),
        timezone='Europe/Moscow',
        organizer_employee_id=organizer.id,
        status=MeetingStatus.active.value,
        reminder_minutes=None,
    )
    db.add(meeting)
    db.flush()
    for participant in participants:
        db.add(MeetingParticipant(meeting_id=meeting.id, employee_id=participant.id))
    db.flush()
    return meeting


def test_draft_create_event_rejects_past_start(db, monkeypatch):
    now = datetime(2026, 5, 2, 3, 10, tzinfo=timezone.utc)
    monkeypatch.setattr('app.calendar_logic.utcnow', lambda: now)

    with pytest.raises(HTTPException) as excinfo:
        draft_create_event(
            db,
            '@dmitry:org1.company.ru',
            'Совещание с шефом',
            None,
            [],
            now - timedelta(minutes=10),
            now + timedelta(minutes=20),
            'Asia/Barnaul',
            15,
            False,
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail['error'] == 'event_start_must_be_in_future'
    assert excinfo.value.detail['start'] == (now - timedelta(minutes=10)).isoformat()


def test_confirm_create_event_rejects_action_that_became_past(db, monkeypatch):
    draft_now = datetime(2026, 5, 2, 3, 10, tzinfo=timezone.utc)
    monkeypatch.setattr('app.calendar_logic.utcnow', lambda: draft_now)
    action = draft_create_event(
        db,
        '@dmitry:org1.company.ru',
        'Short notice event',
        None,
        [],
        draft_now + timedelta(minutes=5),
        draft_now + timedelta(minutes=35),
        'Asia/Barnaul',
        15,
        False,
    )

    confirm_now = draft_now + timedelta(minutes=6)
    monkeypatch.setattr('app.calendar_logic.utcnow', lambda: confirm_now)

    with pytest.raises(HTTPException) as excinfo:
        confirm_pending_action(db, '@dmitry:org1.company.ru', action.id, True)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail['error'] == 'event_start_must_be_in_future'
    assert db.query(Meeting).count() == 0


def test_draft_reschedule_event_rejects_past_new_start(db, monkeypatch):
    now = datetime(2026, 5, 2, 3, 10, tzinfo=timezone.utc)
    monkeypatch.setattr('app.calendar_logic.utcnow', lambda: now)
    dmitry = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry')
    existing = make_meeting(db, dmitry, [dmitry], title='Future event')

    with pytest.raises(HTTPException) as excinfo:
        draft_reschedule_event(
            db,
            dmitry.matrix_id,
            existing.id,
            now - timedelta(minutes=1),
            now + timedelta(minutes=29),
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail['error'] == 'event_start_must_be_in_future'
    assert excinfo.value.detail['new_start'] == (now - timedelta(minutes=1)).isoformat()


def test_get_schedule_returns_freebusy_for_other_calendar(db):
    dmitry = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry')
    pilot = make_employee(db, '@pilot1:org1.company.ru', 'Pilot One')
    meeting = make_meeting(db, pilot, [pilot], title='Private pilot event')

    own = get_schedule(db, pilot.matrix_id, pilot.matrix_id, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc))
    other = get_schedule(db, dmitry.matrix_id, pilot.matrix_id, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc))

    assert own[0]['meeting_id'] == meeting.id
    assert own[0]['title'] == 'Private pilot event'
    assert other == [{
        'meeting_id': None,
        'title': 'Занят',
        'description': None,
        'start': datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc),
        'end': datetime(2026, 5, 5, 7, 0, tzinfo=timezone.utc),
        'timezone': 'Europe/Moscow',
        'participants': [],
    }]


def test_non_organizer_cannot_cancel_meeting(db):
    dmitry = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry')
    pilot = make_employee(db, '@pilot1:org1.company.ru', 'Pilot One')
    meeting = make_meeting(db, pilot, [pilot, dmitry], title='Pilot-owned meeting')

    with pytest.raises(HTTPException) as excinfo:
        draft_cancel_event(db, dmitry.matrix_id, meeting.id)

    assert excinfo.value.status_code == 403


def test_user_can_draft_and_confirm_clear_own_calendar(db):
    dmitry = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry')
    meeting = make_meeting(db, dmitry, [dmitry], title='Own event')

    action = draft_clear_my_calendar(db, dmitry.matrix_id, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert action.action_type == 'clear_calendar'
    assert action.payload['target_employee_id'] == dmitry.id
    assert action.payload['meeting_ids'] == [meeting.id]
    assert action.payload['mode'] == 'self'

    status, message, meeting_id = confirm_pending_action(db, dmitry.matrix_id, action.id, True)

    assert status == 'confirmed'
    assert meeting_id is None
    assert 'Удалено событий: 1' in message
    assert db.get(Meeting, meeting.id).status == MeetingStatus.cancelled.value


def test_user_cannot_admin_clear_another_calendar(db):
    dmitry = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry')
    pilot = make_employee(db, '@pilot1:org1.company.ru', 'Pilot One')
    make_meeting(db, pilot, [pilot], title='Private pilot event')

    with pytest.raises(HTTPException) as excinfo:
        draft_admin_clear_calendar(db, dmitry.matrix_id, pilot.matrix_id, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc), 'test cleanup')

    assert excinfo.value.status_code == 403


def test_calendar_admin_can_clear_another_calendar_with_audit(db):
    admin = make_employee(db, '@dmitry:org1.company.ru', 'Dmitry', role='calendar_admin')
    pilot = make_employee(db, '@pilot1:org1.company.ru', 'Pilot One')
    meeting = make_meeting(db, pilot, [pilot], title='Private pilot event')

    action = draft_admin_clear_calendar(db, admin.matrix_id, pilot.matrix_id, datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc), 'test cleanup')
    assert action.payload['mode'] == 'admin'
    assert action.payload['reason'] == 'test cleanup'

    confirm_pending_action(db, admin.matrix_id, action.id, True)
    db.flush()

    assert db.get(Meeting, meeting.id).status == MeetingStatus.cancelled.value
    audit = db.scalar(select(AuditLog).where(AuditLog.event_type == 'calendar_cleared'))
    assert audit.actor_employee_id == admin.id
    assert audit.target_employee_id == pilot.id
    assert audit.details['reason'] == 'test cleanup'
    assert audit.details['deleted_meeting_ids'] == [meeting.id]
