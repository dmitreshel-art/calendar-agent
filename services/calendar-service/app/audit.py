from sqlalchemy.orm import Session
from .models import AuditLog


def audit(db: Session, event_type: str, actor_employee_id: str | None = None, target_employee_id: str | None = None, details: dict | None = None) -> None:
    db.add(AuditLog(event_type=event_type, actor_employee_id=actor_employee_id, target_employee_id=target_employee_id, details=details or {}))
