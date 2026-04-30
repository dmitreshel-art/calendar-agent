from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class EnsureEmployeeRequest(BaseModel):
    matrix_id: str
    display_name: str | None = None
    email: str | None = None


class EmployeeOut(BaseModel):
    id: str
    matrix_id: str
    matrix_server: str
    localpart: str
    display_name: str | None
    email: str | None
    calendar_path: str
    timezone: str
    workday_start: str
    workday_end: str
    workdays: list[str]
    status: str

    model_config = {"from_attributes": True}


class EmployeePatch(BaseModel):
    display_name: str | None = None
    email: str | None = None
    timezone: str | None = None
    workday_start: str | None = None
    workday_end: str | None = None
    workdays: list[str] | None = None


class ParticipantRef(BaseModel):
    matrix_id: str | None = None
    query: str | None = None


class GetScheduleRequest(BaseModel):
    requester_matrix_id: str
    target_matrix_id: str | None = None
    start: datetime
    end: datetime


class CalendarEventOut(BaseModel):
    meeting_id: int | None = None
    title: str
    description: str | None = None
    start: datetime
    end: datetime
    timezone: str | None = None
    participants: list[str] = Field(default_factory=list)


class FindFreeSlotsRequest(BaseModel):
    requester_matrix_id: str
    participants: list[ParticipantRef] = Field(default_factory=list)
    duration_minutes: int = Field(30, gt=0, le=24 * 60)
    start: datetime | None = None
    end: datetime | None = None
    max_slots: int | None = Field(default=None, gt=0, le=20)


class FreeSlotOut(BaseModel):
    start: datetime
    end: datetime
    participants: list[str]


class DraftCreateEventRequest(BaseModel):
    requester_matrix_id: str
    title: str | None = None
    description: str | None = None
    participants: list[ParticipantRef] = Field(default_factory=list)
    start: datetime
    end: datetime
    timezone: str | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=60 * 24 * 30)
    no_reminder: bool = False


class DraftRescheduleEventRequest(BaseModel):
    requester_matrix_id: str
    meeting_id: int
    new_start: datetime
    new_end: datetime


class DraftCancelEventRequest(BaseModel):
    requester_matrix_id: str
    meeting_id: int


class PendingActionOut(BaseModel):
    id: str
    requester_employee_id: str
    action_type: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
    expires_at: datetime | None

    model_config = {"from_attributes": True}


class ConfirmPendingActionRequest(BaseModel):
    requester_matrix_id: str
    pending_action_id: str
    confirm: bool = True


class ConfirmResult(BaseModel):
    status: str
    message: str
    meeting_id: int | None = None


class SearchEmployeesRequest(BaseModel):
    requester_matrix_id: str
    query: str
    limit: int = 10


class ReminderOut(BaseModel):
    id: int
    matrix_id: str
    body: str


class AgentMessageRequest(BaseModel):
    matrix_id: str
    message: str
    display_name: str | None = None
    email: str | None = None
    conversation_id: str | None = None


class AgentMessageResponse(BaseModel):
    status: str
    reply: str
    pending_action_id: str | None = None
    meeting_id: int | None = None
    data: dict[str, Any] | None = None


class HealthOut(BaseModel):
    status: Literal['ok']
    version: str
