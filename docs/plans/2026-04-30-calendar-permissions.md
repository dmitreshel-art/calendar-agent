# Calendar Permissions Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make Calendar Agent, not Hermes or LLM, the authoritative policy layer for calendar privacy, event modification, and admin calendar cleanup.

**Architecture:** Hermes remains transport; the LLM produces structured intents; Calendar Agent core validates every operation through deterministic policy functions before creating pending actions or touching Radicale. User operations use `/tools/*` and `AGENT_API_TOKEN`; admin operations use `/admin/*`, `ADMIN_API_TOKEN`, explicit actor/reason, confirmation, and audit log.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite via `Base.metadata.create_all`, pytest, Radicale/CalDAV.

---

## Current code facts

- Repo: `/root/calendar-agent`, service code: `/root/calendar-agent/services/calendar-service`.
- `Meeting.organizer_employee_id` already exists in `app/models.py` and create/reschedule/cancel logic already uses organizer for MVP.
- `Employee.role` does **not** exist yet.
- There is no Alembic; `init_db()` only calls `Base.metadata.create_all()`, so existing SQLite DBs need a small additive migration helper for new columns.
- Existing privacy behavior in `get_schedule()` already returns free/busy (`title='Занят'`, `meeting_id=None`) for another user's calendar.
- Admin endpoints currently authenticate only by `ADMIN_API_TOKEN`; they do not know the human admin actor unless request body includes it.

---

## Target policy

1. **User calendar read**
   - requester == target: full details.
   - requester != target: free/busy only.

2. **Create event**
   - requester is always included as participant.
   - service must reject any create payload where requester would not be included.

3. **Reschedule/cancel event**
   - allowed if requester is organizer.
   - later extension: delegated calendar manager.
   - denied for ordinary participant or unrelated user.

4. **Clear own calendar**
   - `/tools/draft-clear-my-calendar`: requester may clear only own calendar in an explicit date range.
   - must be draft/confirm.

5. **Clear someone else's calendar**
   - only `/admin/draft-clear-calendar` with `ADMIN_API_TOKEN`.
   - body must include `admin_matrix_id`, `target_matrix_id`, `start`, `end`, `reason`.
   - admin actor must have `Employee.role == 'calendar_admin'`.
   - must be draft/confirm.
   - must audit request and execution.

---

## Task 1: Add employee roles to model and schema

**Objective:** Store whether an employee is a normal user or calendar admin.

**Files:**
- Modify: `services/calendar-service/app/models.py`
- Modify: `services/calendar-service/app/schemas.py`
- Test: `services/calendar-service/tests/test_permissions.py`

**Step 1: Write failing tests**

Create `services/calendar-service/tests/test_permissions.py`:

```python
from app.models import EmployeeRole


def test_employee_role_values_are_stable():
    assert EmployeeRole.user.value == 'user'
    assert EmployeeRole.calendar_admin.value == 'calendar_admin'
```

**Step 2: Run test to verify failure**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_permissions.py::test_employee_role_values_are_stable
```

Expected: fail because `EmployeeRole` does not exist.

**Step 3: Implement model role enum and column**

In `app/models.py`, add after `EmployeeStatus`:

```python
class EmployeeRole(str, enum.Enum):
    user = 'user'
    calendar_admin = 'calendar_admin'
```

In `Employee`, add near `status`:

```python
role: Mapped[str] = mapped_column(String(30), default=EmployeeRole.user.value)
```

In `app/schemas.py`, add to `EmployeeOut`:

```python
role: str = 'user'
```

Add to `EmployeePatch`:

```python
role: str | None = None
```

**Step 4: Run test to verify pass**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_permissions.py::test_employee_role_values_are_stable
```

Expected: pass.

**Step 5: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/models.py services/calendar-service/app/schemas.py services/calendar-service/tests/test_permissions.py
git commit -m "feat(calendar): add employee roles"
```

---

## Task 2: Add additive SQLite migration for employee.role

**Objective:** Existing pilot DB must gain the `employees.role` column without dropping data.

**Files:**
- Modify: `services/calendar-service/app/database.py`
- Test: `services/calendar-service/tests/test_database_migrations.py`

**Step 1: Write failing migration test**

Create `tests/test_database_migrations.py`:

```python
import sqlite3

from app.database import ensure_sqlite_schema_columns


def test_ensure_sqlite_schema_columns_adds_employee_role(tmp_path):
    db_path = tmp_path / 'app.db'
    con = sqlite3.connect(db_path)
    con.execute('create table employees (id text primary key, matrix_id text)')
    con.commit()
    con.close()

    ensure_sqlite_schema_columns(f'sqlite:///{db_path}')

    con = sqlite3.connect(db_path)
    cols = [row[1] for row in con.execute('pragma table_info(employees)').fetchall()]
    default_row = con.execute("select dflt_value from pragma_table_info('employees') where name='role'").fetchone()
    con.close()

    assert 'role' in cols
    assert default_row[0] == "'user'"
```

**Step 2: Run test to verify failure**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_database_migrations.py
```

Expected: fail because helper does not exist.

**Step 3: Implement additive migration helper**

In `app/database.py`:

```python
from sqlalchemy import text


def ensure_sqlite_schema_columns(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith('sqlite'):
        return
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(employees)").fetchall()}
        if 'role' not in existing:
            conn.exec_driver_sql("ALTER TABLE employees ADD COLUMN role VARCHAR(30) DEFAULT 'user' NOT NULL")
```

Then update `init_db()`:

```python
def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema_columns()
```

Important: if the test uses a temp DB URL but global `engine` points elsewhere, refactor helper to create a temporary engine for the provided URL:

```python
def ensure_sqlite_schema_columns(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith('sqlite'):
        return
    migration_engine = engine if database_url is None else create_engine(url, connect_args={"check_same_thread": False})
    with migration_engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(employees)").fetchall()}
        if 'role' not in existing:
            conn.exec_driver_sql("ALTER TABLE employees ADD COLUMN role VARCHAR(30) DEFAULT 'user' NOT NULL")
```

**Step 4: Run test to verify pass**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_database_migrations.py
```

Expected: pass.

**Step 5: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/database.py services/calendar-service/tests/test_database_migrations.py
git commit -m "feat(calendar): migrate employee role column"
```

---

## Task 3: Add deterministic policy layer

**Objective:** Centralize authorization rules outside LLM and route handlers.

**Files:**
- Create: `services/calendar-service/app/policy.py`
- Test: `services/calendar-service/tests/test_policy.py`

**Step 1: Write failing tests**

Create `tests/test_policy.py`:

```python
from types import SimpleNamespace

from app.policy import can_admin_clear_calendar, can_clear_own_calendar, can_modify_meeting, can_view_details


def emp(id, role='user'):
    return SimpleNamespace(id=id, role=role)


def meeting(organizer_id='u1'):
    return SimpleNamespace(organizer_employee_id=organizer_id)


def test_user_sees_own_details_only():
    assert can_view_details(emp('u1'), emp('u1')) is True
    assert can_view_details(emp('u1'), emp('u2')) is False


def test_only_organizer_can_modify_meeting():
    assert can_modify_meeting(emp('u1'), meeting('u1')) is True
    assert can_modify_meeting(emp('u2'), meeting('u1')) is False


def test_user_can_clear_only_own_calendar():
    assert can_clear_own_calendar(emp('u1'), emp('u1')) is True
    assert can_clear_own_calendar(emp('u1'), emp('u2')) is False


def test_only_calendar_admin_can_admin_clear_calendar():
    assert can_admin_clear_calendar(emp('u1', 'calendar_admin'), emp('u2')) is True
    assert can_admin_clear_calendar(emp('u1', 'user'), emp('u2')) is False
```

**Step 2: Run test to verify failure**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_policy.py
```

Expected: fail because `app.policy` does not exist.

**Step 3: Implement `app/policy.py`**

```python
from .models import Employee, EmployeeRole, Meeting


def is_calendar_admin(employee: Employee) -> bool:
    return employee.role == EmployeeRole.calendar_admin.value


def can_view_details(requester: Employee, target: Employee) -> bool:
    return requester.id == target.id


def can_modify_meeting(requester: Employee, meeting: Meeting) -> bool:
    return requester.id == meeting.organizer_employee_id or is_calendar_admin(requester)


def can_clear_own_calendar(requester: Employee, target: Employee) -> bool:
    return requester.id == target.id


def can_admin_clear_calendar(admin: Employee, target: Employee) -> bool:
    return is_calendar_admin(admin)
```

Note: `can_modify_meeting` may allow admin modification through future admin endpoints, but ordinary `/tools/*` should still only pass requester users and use this helper deliberately.

**Step 4: Run test to verify pass**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_policy.py
```

Expected: pass.

**Step 5: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/policy.py services/calendar-service/tests/test_policy.py
git commit -m "feat(calendar): add calendar policy layer"
```

---

## Task 4: Refactor existing read/modify logic to use policy helpers

**Objective:** Existing behavior should be enforced through `policy.py`, not ad-hoc checks.

**Files:**
- Modify: `services/calendar-service/app/calendar_logic.py`
- Test: extend `services/calendar-service/tests/test_policy.py` or create `tests/test_calendar_permissions.py`

**Step 1: Write tests for schedule privacy and organizer-only cancel**

Use unit-style tests with an in-memory DB if existing fixture support is absent. Minimum assertions:

- `get_schedule(requester=target)` returns real title and meeting_id.
- `get_schedule(requester!=target)` returns `title == 'Занят'` and `meeting_id is None`.
- `draft_cancel_event(non_organizer, meeting_id)` raises `HTTPException(403)`.

**Step 2: Refactor**

In `calendar_logic.py`, import:

```python
from .policy import can_modify_meeting, can_view_details
```

Replace direct checks:

```python
if requester.id != meeting.organizer_employee_id:
    raise HTTPException(...)
```

with:

```python
if not can_modify_meeting(requester, meeting):
    raise HTTPException(status_code=403, detail='Only organizer can modify the meeting')
```

In `get_schedule()` replace:

```python
if target.id != requester.id:
```

with:

```python
if not can_view_details(requester, target):
```

**Step 3: Run targeted tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_policy.py tests/test_calendar_permissions.py
```

Expected: pass.

**Step 4: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/calendar_logic.py services/calendar-service/tests/test_policy.py services/calendar-service/tests/test_calendar_permissions.py
git commit -m "refactor(calendar): enforce access through policy layer"
```

---

## Task 5: Add schemas for calendar clear operations

**Objective:** Define explicit request models for own and admin calendar cleanup.

**Files:**
- Modify: `services/calendar-service/app/schemas.py`
- Test: `services/calendar-service/tests/test_clear_calendar_schemas.py`

**Step 1: Write failing tests**

```python
from datetime import datetime, timezone

from app.schemas import AdminClearCalendarRequest, ClearMyCalendarRequest


def test_clear_my_calendar_schema_has_date_range():
    req = ClearMyCalendarRequest(
        requester_matrix_id='@dmitry:org1.company.ru',
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert req.requester_matrix_id == '@dmitry:org1.company.ru'


def test_admin_clear_calendar_schema_requires_reason():
    req = AdminClearCalendarRequest(
        admin_matrix_id='@dmitry:org1.company.ru',
        target_matrix_id='@pilot1:org1.company.ru',
        start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 1, tzinfo=timezone.utc),
        reason='сброс тестовых данных',
    )
    assert req.reason == 'сброс тестовых данных'
```

**Step 2: Implement schemas**

In `schemas.py`:

```python
class ClearMyCalendarRequest(BaseModel):
    requester_matrix_id: str
    start: datetime
    end: datetime


class AdminClearCalendarRequest(BaseModel):
    admin_matrix_id: str
    target_matrix_id: str
    start: datetime
    end: datetime
    reason: str = Field(min_length=5, max_length=500)
```

**Step 3: Run tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_clear_calendar_schemas.py
```

**Step 4: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/schemas.py services/calendar-service/tests/test_clear_calendar_schemas.py
git commit -m "feat(calendar): add clear calendar request schemas"
```

---

## Task 6: Implement draft clear calendar logic

**Objective:** Create pending actions for own/admin calendar cleanup without immediately deleting anything.

**Files:**
- Modify: `services/calendar-service/app/calendar_logic.py`
- Test: `services/calendar-service/tests/test_clear_calendar_logic.py`

**Step 1: Add helper to find target meetings**

In `calendar_logic.py` add:

```python
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
```

**Step 2: Implement own clear draft**

```python
def draft_clear_my_calendar(db: Session, requester_matrix_id: str, start: datetime, end: datetime) -> PendingAction:
    requester = ensure_employee(db, requester_matrix_id)
    start_u = _aware_utc(start)
    end_u = _aware_utc(end)
    if end_u <= start_u:
        raise HTTPException(status_code=400, detail='end must be after start')
    meeting_ids = _meeting_ids_for_employee_range(db, requester, start_u, end_u)
    payload = {
        'target_employee_id': requester.id,
        'start': start_u.isoformat(),
        'end': end_u.isoformat(),
        'meeting_ids': meeting_ids,
        'mode': 'self',
    }
    return create_pending_action(db, requester, 'clear_calendar', payload)
```

**Step 3: Implement admin clear draft**

```python
def draft_admin_clear_calendar(db: Session, admin_matrix_id: str, target_matrix_id: str, start: datetime, end: datetime, reason: str) -> PendingAction:
    admin = ensure_employee(db, admin_matrix_id)
    target = ensure_employee(db, target_matrix_id)
    if not can_admin_clear_calendar(admin, target):
        raise HTTPException(status_code=403, detail='Only calendar admin can clear another calendar')
    start_u = _aware_utc(start)
    end_u = _aware_utc(end)
    if end_u <= start_u:
        raise HTTPException(status_code=400, detail='end must be after start')
    meeting_ids = _meeting_ids_for_employee_range(db, target, start_u, end_u)
    payload = {
        'target_employee_id': target.id,
        'target_matrix_id': target.matrix_id,
        'start': start_u.isoformat(),
        'end': end_u.isoformat(),
        'meeting_ids': meeting_ids,
        'mode': 'admin',
        'reason': reason.strip(),
    }
    return create_pending_action(db, admin, 'clear_calendar', payload)
```

**Step 4: Tests**

Add tests that verify:

- ordinary user cannot call `draft_admin_clear_calendar` unless role is `calendar_admin`.
- own clear draft includes only own target employee id.
- empty range still creates pending action with `meeting_ids == []` so UX can say nothing to delete.

**Step 5: Run tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_clear_calendar_logic.py
```

**Step 6: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/calendar_logic.py services/calendar-service/tests/test_clear_calendar_logic.py
git commit -m "feat(calendar): draft calendar clear actions"
```

---

## Task 7: Materialize clear calendar pending action safely

**Objective:** Confirming a clear-calendar action cancels matching meetings and deletes Radicale event copies.

**Files:**
- Modify: `services/calendar-service/app/calendar_logic.py`
- Test: `services/calendar-service/tests/test_clear_calendar_logic.py`

**Important design choice:** For MVP, clearing a user's calendar cancels each selected `Meeting` globally, not just removes that user's copy. This is acceptable for admin/test cleanup but should be clearly documented. If later we need “remove only one participant's copy”, add a separate `remove_participant_calendar_copy` action.

**Step 1: Implement materializer**

```python
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
```

**Step 2: Wire into `confirm_pending_action()`**

Add before `else`:

```python
elif action.action_type == 'clear_calendar':
    deleted = _materialize_clear_calendar(db, action)
    message = f'Календарь очищен. Удалено событий: {len(deleted)}'
```

`meeting_id` stays `None`.

**Step 3: Tests**

Mock `RadicaleClient.delete_event` to avoid network. Assert:

- meeting status becomes `cancelled`.
- unsent reminders are removed.
- `calendar_cleared` audit row exists with target and reason.
- confirming a stale action skips already-cancelled meetings without error.

**Step 4: Run tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_clear_calendar_logic.py
```

**Step 5: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/calendar_logic.py services/calendar-service/tests/test_clear_calendar_logic.py
git commit -m "feat(calendar): confirm clear calendar actions"
```

---

## Task 8: Add API endpoints

**Objective:** Expose safe clear operations through explicit user/admin endpoints.

**Files:**
- Modify: `services/calendar-service/app/main.py`
- Modify: `services/calendar-service/app/schemas.py` imports if needed
- Test: `services/calendar-service/tests/test_clear_calendar_api.py`

**Step 1: Update imports in `main.py`**

Add schemas:

```python
ClearMyCalendarRequest, AdminClearCalendarRequest,
```

Add logic functions:

```python
from .calendar_logic import ..., draft_clear_my_calendar, draft_admin_clear_calendar
```

**Step 2: Add user endpoint**

```python
@app.post('/tools/draft-clear-my-calendar', response_model=PendingActionOut, dependencies=[Depends(require_agent_token)])
def api_draft_clear_my_calendar(req: ClearMyCalendarRequest, db: Session = Depends(get_db)):
    action = draft_clear_my_calendar(db, req.requester_matrix_id, req.start, req.end)
    db.commit()
    db.refresh(action)
    return action
```

**Step 3: Add admin endpoint**

```python
@app.post('/admin/draft-clear-calendar', response_model=PendingActionOut, dependencies=[Depends(require_admin_token)])
def admin_draft_clear_calendar(req: AdminClearCalendarRequest, db: Session = Depends(get_db)):
    action = draft_admin_clear_calendar(db, req.admin_matrix_id, req.target_matrix_id, req.start, req.end, req.reason)
    db.commit()
    db.refresh(action)
    return action
```

Keep confirmation via existing `/tools/confirm-pending-action` for now. If stricter separation is desired, add `/admin/confirm-pending-action` later; not needed for MVP because action owner/admin must confirm with their matrix id and possession of agent token. For stricter admin-only confirmation, add it as follow-up.

**Step 4: API tests**

Use FastAPI `TestClient` if available; otherwise function-level tests are enough for MVP. Assert:

- `/tools/draft-clear-my-calendar` requires agent token.
- `/admin/draft-clear-calendar` requires admin token.
- admin endpoint returns 403 when `admin_matrix_id` role is `user`.

**Step 5: Run tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_clear_calendar_api.py
```

**Step 6: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/main.py services/calendar-service/app/schemas.py services/calendar-service/tests/test_clear_calendar_api.py
git commit -m "feat(calendar): expose clear calendar endpoints"
```

---

## Task 9: Update LLM agent flow to recognize but not authorize clear-calendar intents

**Objective:** The LLM can parse the user's request, but deterministic Calendar Agent code decides policy.

**Files:**
- Modify: `services/calendar-service/app/agent.py`
- Test: `services/calendar-service/tests/test_agent_clear_calendar.py`

**Step 1: Inspect existing intent handling**

Read `app/agent.py` and locate where intents are mapped to `draft_create_event`, `draft_cancel_event`, etc.

**Step 2: Add intent handling rule**

Desired behavior:

- “очисти мой календарь за май” → call `draft_clear_my_calendar`, return pending confirmation.
- “очисти календарь первого пилота” → if parsed as target != requester, reply with denial/advice unless phrase clearly says admin and requester is admin.
- “как администратор очисти календарь первого пилота за май, причина: …” → call `draft_admin_clear_calendar` only if role is admin; otherwise 403/denial.

**Step 3: Test cases**

- ordinary user asks to clear another calendar → response contains “нельзя” or “администратор”.
- ordinary user asks to clear own calendar → pending action created.
- admin asks admin clear with reason → pending action created with `mode='admin'`.

**Step 4: Run tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q tests/test_agent_clear_calendar.py
```

**Step 5: Commit**

```bash
cd /root/calendar-agent
git add services/calendar-service/app/agent.py services/calendar-service/tests/test_agent_clear_calendar.py
git commit -m "feat(calendar): parse clear calendar intents safely"
```

---

## Task 10: Full verification and smoke

**Objective:** Prove the feature works without breaking the existing pilot stack.

**Files:**
- Maybe modify: `scripts/smoke.sh` to add one permission check if appropriate.

**Step 1: Run unit tests**

```bash
cd /root/calendar-agent/services/calendar-service
/tmp/calendar-agent-venv/bin/python -m pytest -q
```

Expected: all tests pass.

**Step 2: Run shell syntax checks**

```bash
cd /root/calendar-agent
bash -n scripts/smoke.sh scripts/init-radicale-users.sh
```

Expected: no output, exit 0.

**Step 3: Rebuild stack and wait for health**

```bash
cd /root/calendar-agent
docker compose -p calendar-agent-pilot up -d --build
for i in $(seq 1 40); do curl -fsS http://localhost:8080/health >/dev/null && break; sleep 1; done
curl -fsS http://localhost:8080/health
```

Expected: `{"status":"ok","version":"0.2.1"}` or bumped version.

**Step 4: Run existing smoke**

```bash
cd /root/calendar-agent
./scripts/smoke.sh
```

Expected: pass.

**Step 5: Manual permission checks with redacted tokens**

Use token from `.env`, but do not print it.

- Ordinary Dmitry cannot admin-clear pilot calendar until role is calendar_admin.
- After patching Dmitry role through admin endpoint or DB for test, admin draft works.
- Confirming draft cancels selected events and writes `calendar_cleared` audit log.

**Step 6: Commit smoke/doc updates**

```bash
cd /root/calendar-agent
git add scripts/smoke.sh docs/plans/2026-04-30-calendar-permissions.md
git commit -m "test(calendar): add permission smoke coverage"
```

---

## Follow-ups deliberately out of MVP

- Delegated calendar access (`calendar_delegate`).
- Department schedulers.
- Removing only one participant's calendar copy without cancelling the meeting globally.
- Separate `/admin/confirm-pending-action` endpoint.
- Alembic migration framework.
- UI/Element buttons for dangerous confirmations.

---

## Acceptance criteria

- [ ] Calendar Agent owns all permission decisions.
- [ ] LLM can only propose intent; it cannot bypass policy functions.
- [ ] Ordinary user sees other calendars only as free/busy.
- [ ] Ordinary user cannot clear another user's calendar.
- [ ] Ordinary user can draft/confirm clearing own calendar for an explicit range.
- [ ] Admin can draft/confirm clearing another user's calendar only with `ADMIN_API_TOKEN`, `calendar_admin` role, reason, and audit log.
- [ ] Existing create/reschedule/cancel flows still pass.
- [ ] Unit tests and Docker smoke pass.
