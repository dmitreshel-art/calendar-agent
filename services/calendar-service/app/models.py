import enum
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Integer, Text, Boolean, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmployeeStatus(str, enum.Enum):
    active = 'active'
    blocked = 'blocked'


class PendingActionStatus(str, enum.Enum):
    pending = 'pending'
    confirmed = 'confirmed'
    cancelled = 'cancelled'
    expired = 'expired'


class DialogStateStatus(str, enum.Enum):
    active = 'active'
    resolved = 'resolved'
    cancelled = 'cancelled'
    expired = 'expired'


class MeetingStatus(str, enum.Enum):
    active = 'active'
    cancelled = 'cancelled'


class Employee(Base):
    __tablename__ = 'employees'

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    matrix_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    matrix_server: Mapped[str] = mapped_column(String(255), index=True)
    localpart: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calendar_path: Mapped[str] = mapped_column(String(500), unique=True)
    timezone: Mapped[str] = mapped_column(String(100))
    workday_start: Mapped[str] = mapped_column(String(5))
    workday_end: Mapped[str] = mapped_column(String(5))
    workdays: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=EmployeeStatus.active.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Meeting(Base):
    __tablename__ = 'meetings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(100))
    organizer_employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'))
    status: Mapped[str] = mapped_column(String(20), default=MeetingStatus.active.value)
    reminder_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    participants: Mapped[list['MeetingParticipant']] = relationship(cascade='all, delete-orphan')
    event_copies: Mapped[list['EventCopy']] = relationship(cascade='all, delete-orphan')


class MeetingParticipant(Base):
    __tablename__ = 'meeting_participants'
    __table_args__ = (UniqueConstraint('meeting_id', 'employee_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey('meetings.id'))
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'))


class EventCopy(Base):
    __tablename__ = 'event_copies'
    __table_args__ = (UniqueConstraint('meeting_id', 'employee_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey('meetings.id'))
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'))
    calendar_path: Mapped[str] = mapped_column(String(500))
    event_uid: Mapped[str] = mapped_column(String(255), index=True)


class PendingAction(Base):
    __tablename__ = 'pending_actions'

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    requester_employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'))
    action_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=PendingActionStatus.pending.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DialogState(Base):
    __tablename__ = 'dialog_states'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requester_employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'), index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), index=True)
    state_type: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default=DialogStateStatus.active.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Reminder(Base):
    __tablename__ = 'reminders'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey('meetings.id'))
    employee_id: Mapped[str] = mapped_column(ForeignKey('employees.id'))
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str] = mapped_column(Text)


class OutboxMessage(Base):
    __tablename__ = 'outbox_messages'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matrix_id: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = 'audit_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    actor_employee_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_employee_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
