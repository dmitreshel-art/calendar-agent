import re
import hashlib
from urllib.parse import quote
from datetime import datetime, timezone
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from .config import get_settings
from .models import Employee, EmployeeStatus
from .audit import audit
from .radicale_client import RadicaleClient

MXID_RE = re.compile(r'^@(?P<localpart>[^:]+):(?P<server>.+)$')


def parse_mxid(matrix_id: str) -> tuple[str, str]:
    m = MXID_RE.match(matrix_id.strip())
    if not m:
        raise HTTPException(status_code=400, detail=f'Invalid Matrix ID: {matrix_id}')
    return m.group('localpart'), m.group('server').lower()


def _safe_id_part(value: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9]+', '_', value).strip('_').lower()
    return safe or 'user'


def employee_id_from_mxid(matrix_id: str) -> str:
    # Keep the ID readable but add a hash suffix to avoid collisions such as
    # @ivanov-1:org and @ivanov_1:org after normalization.
    localpart, server = parse_mxid(matrix_id)
    digest = hashlib.sha1(matrix_id.encode('utf-8')).hexdigest()[:10]
    base = f'{_safe_id_part(server)}__{_safe_id_part(localpart)}'
    return f'{base}__{digest}'[:200]


def _path_part(value: str) -> str:
    return quote(value, safe='')


def calendar_path_from_mxid(matrix_id: str) -> str:
    localpart, server = parse_mxid(matrix_id)
    root = get_settings().radicale_calendar_root.strip('/') or 'calendars'
    return f'/{root}/{_path_part(server)}/{_path_part(localpart)}/default'


def assert_allowed_matrix_id(matrix_id: str) -> None:
    settings = get_settings()
    localpart, server = parse_mxid(matrix_id)
    if matrix_id in settings.blocked_users_list:
        raise HTTPException(status_code=403, detail='Matrix user is blocked by configuration')
    if server not in settings.allowed_servers_list:
        if settings.disable_unknown_homeservers:
            raise HTTPException(status_code=403, detail=f'Matrix homeserver is not allowed: {server}')


def ensure_employee(db: Session, matrix_id: str, display_name: str | None = None, email: str | None = None) -> Employee:
    assert_allowed_matrix_id(matrix_id)
    existing = db.scalar(select(Employee).where(Employee.matrix_id == matrix_id))
    if existing:
        existing.last_seen_at = datetime.now(timezone.utc)
        if display_name and not existing.display_name:
            existing.display_name = display_name
        if email and not existing.email:
            existing.email = email
        if existing.status == EmployeeStatus.blocked.value:
            raise HTTPException(status_code=403, detail='Employee is blocked')
        return existing

    settings = get_settings()
    if not settings.auto_provision_employees:
        raise HTTPException(status_code=404, detail='Employee does not exist and auto provisioning is disabled')

    localpart, server = parse_mxid(matrix_id)
    employee = Employee(
        id=employee_id_from_mxid(matrix_id),
        matrix_id=matrix_id,
        matrix_server=server,
        localpart=localpart,
        display_name=display_name or localpart,
        email=email,
        calendar_path=calendar_path_from_mxid(matrix_id),
        timezone=settings.default_timezone,
        workday_start=settings.default_workday_start,
        workday_end=settings.default_workday_end,
        workdays=settings.workdays_list,
        status=EmployeeStatus.active.value,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(employee)
    db.flush()
    audit(db, 'employee_auto_provisioned', actor_employee_id=employee.id, target_employee_id=employee.id, details={'matrix_id': matrix_id})
    # The MVP contract says the employee calendar is created during auto-provisioning,
    # not lazily on the first event. Strict Radicale writes are enabled by default.
    RadicaleClient().ensure_calendar(employee.calendar_path, employee.display_name)
    audit(db, 'calendar_created', actor_employee_id=employee.id, target_employee_id=employee.id, details={'calendar_path': employee.calendar_path})
    return employee


def find_employee(db: Session, ref: str) -> list[Employee]:
    ref = ref.strip()
    if ref.startswith('@'):
        emp = db.scalar(select(Employee).where(Employee.matrix_id == ref))
        return [emp] if emp else []
    like = f'%{ref}%'
    return list(db.scalars(select(Employee).where(or_(Employee.display_name.ilike(like), Employee.localpart.ilike(like), Employee.matrix_id.ilike(like))).limit(10)))


def resolve_participant(db: Session, matrix_id: str | None = None, query: str | None = None) -> Employee:
    if matrix_id:
        return ensure_employee(db, matrix_id)
    if not query:
        raise HTTPException(status_code=400, detail='Participant requires matrix_id or query')
    matches = find_employee(db, query)
    if not matches:
        raise HTTPException(status_code=404, detail=f'No employee found for query: {query}')
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail={'message': 'Multiple employees found', 'matches': [{'id': e.id, 'matrix_id': e.matrix_id, 'display_name': e.display_name} for e in matches]})
    if matches[0].status == EmployeeStatus.blocked.value:
        raise HTTPException(status_code=403, detail='Employee is blocked')
    return matches[0]
