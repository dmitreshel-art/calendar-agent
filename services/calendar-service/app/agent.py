import copy
import json
import re
from datetime import datetime, timedelta, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calendar_logic import (
    create_pending_action,
    draft_cancel_event,
    draft_create_event,
    draft_reschedule_event,
    free_slots,
    get_schedule,
    confirm_pending_action,
)
from .config import get_settings
from .employees import ensure_employee, find_employee
from .models import (
    DialogState,
    DialogStateStatus,
    Employee,
    Meeting,
    MeetingParticipant,
    MeetingStatus,
    PendingAction,
    PendingActionStatus,
)
from .schemas import ParticipantRef

AFFIRMATIVE = {'да', 'д', 'yes', 'y', 'ok', 'ок', 'окей', 'ага', 'подтверждаю', 'создать', 'перенести', 'отменить', '+'}
NEGATIVE = {'нет', 'не', 'no', 'n', 'отмена', 'отмени', 'не надо', 'cancel', '-'}
SELECTION_RE = re.compile(r'^(?:вариант\s*)?(?P<num>[1-9][0-9]?)$', re.IGNORECASE)
DEFAULT_CONVERSATION_ID = '__default__'


class AgentReply(dict):
    """Lightweight dict response used by /agent/message and MCP wrapper."""


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f'LLM returned invalid datetime: {value}') from exc


def _conversation_key(conversation_id: str | None) -> str:
    value = (conversation_id or '').strip()
    return value or DEFAULT_CONVERSATION_ID


def _local_date_range(employee: Employee, period: str = 'today') -> tuple[datetime, datetime]:
    tz = ZoneInfo(employee.timezone)
    now = datetime.now(tz)
    today = now.date()
    if period == 'tomorrow':
        day = today + timedelta(days=1)
        start = datetime.combine(day, time(0, 0), tzinfo=tz)
        end = start + timedelta(days=1)
    elif period in {'week', 'this_week'}:
        start_day = today - timedelta(days=today.weekday())
        start = datetime.combine(start_day, time(0, 0), tzinfo=tz)
        end = start + timedelta(days=7)
    else:
        start = datetime.combine(today, time(0, 0), tzinfo=tz)
        end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _format_dt(dt: datetime, tz_name: str) -> str:
    local = _aware_utc(dt).astimezone(ZoneInfo(tz_name))
    return local.strftime('%d.%m.%Y %H:%M')


def _format_interval(start: datetime, end: datetime, tz_name: str) -> str:
    return f'{_format_dt(start, tz_name)}–{_aware_utc(end).astimezone(ZoneInfo(tz_name)).strftime("%H:%M")}'


def _is_affirmative(message: str) -> bool:
    return message.strip().lower() in AFFIRMATIVE


def _is_negative(message: str) -> bool:
    return message.strip().lower() in NEGATIVE


def _selection_number(message: str) -> int | None:
    match = SELECTION_RE.match(message.strip())
    if not match:
        lowered = message.strip().lower()
        words = {
            'первый': 1, 'первое': 1, 'первая': 1,
            'второй': 2, 'второе': 2, 'вторая': 2,
            'третий': 3, 'третье': 3, 'третья': 3,
        }
        return words.get(lowered)
    return int(match.group('num'))


def _participant_refs(intent: dict[str, Any]) -> list[ParticipantRef]:
    refs: list[ParticipantRef] = []
    for matrix_id in intent.get('participant_matrix_ids') or []:
        if matrix_id:
            refs.append(ParticipantRef(matrix_id=str(matrix_id)))
    for query in intent.get('participant_queries') or []:
        if query:
            refs.append(ParticipantRef(query=str(query)))
    return refs


def _clone_intent(intent: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(intent)


def _call_llm(message: str, requester: Employee) -> dict[str, Any]:
    settings = get_settings()
    if not settings.llm_api_key or settings.llm_api_key == 'change-me':
        raise HTTPException(status_code=503, detail='LLM is not configured. Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.')

    now_local = datetime.now(ZoneInfo(requester.timezone)).isoformat()
    system_prompt = f"""
Ты календарный NLU-модуль. Верни строго один JSON без markdown.
Текущие дата/время пользователя: {now_local}. Часовой пояс: {requester.timezone}.
Основной язык пользователя: русский.

Схема JSON:
{{
  "intent": "get_schedule|search_employees|find_free_slots|create_event|reschedule_event|cancel_event|unknown",
  "reply": null,
  "target_query": null,
  "participant_queries": [],
  "participant_matrix_ids": [],
  "title": null,
  "description": null,
  "start": null,
  "end": null,
  "date_range_start": null,
  "date_range_end": null,
  "duration_minutes": null,
  "reminder_minutes": null,
  "no_reminder": false,
  "meeting_id": null,
  "new_start": null,
  "new_end": null,
  "needs_free_slot_search": false
}}

Правила:
- Все даты и время возвращай в ISO 8601 с timezone offset.
- Если пользователь просит "что у меня сегодня/завтра/на неделе", intent=get_schedule и заполни date_range_start/date_range_end.
- Если просит найти сотрудника, intent=search_employees и target_query.
- Если просит найти окно, intent=find_free_slots, participant_queries, duration_minutes, date_range_start/date_range_end если есть.
- Если просит создать встречу/событие и точное время известно, intent=create_event, start/end, участники, reminder_minutes/no_reminder.
- Если просит создать встречу без точного времени, но с периодом, intent=create_event, needs_free_slot_search=true, date_range_start/date_range_end, duration_minutes.
- Для личного события participant_queries оставь пустым.
- Если пользователь не указал длительность встречи, используй 30 минут.
- Если сказано "после обеда", интерпретируй как период 13:00-18:00 в date_range_start/date_range_end и needs_free_slot_search=true, если точного времени нет.
- Если просит перенести/отменить, используй meeting_id только если он явно указан. Иначе заполни participant_queries/title/date_range, чтобы система могла найти кандидатов.
- Не придумывай сотрудников. Имена клади в participant_queries.
- Если не понял, intent=unknown и reply на русском.
""".strip()

    url = settings.llm_base_url.rstrip('/') + '/chat/completions'
    payload = {
        'model': settings.llm_model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': message},
        ],
        'temperature': 0,
    }
    try:
        resp = requests.post(
            url,
            headers={'Authorization': f'Bearer {settings.llm_api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=settings.llm_timeout_seconds,
        )
        resp.raise_for_status()
        content = resp.json()['choices'][0]['message']['content']
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'LLM request failed: {exc}') from exc

    content = content.strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f'LLM returned non-JSON response: {content[:300]}') from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail='LLM returned JSON that is not an object')
    return parsed


def _expire_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _state_expired(state: DialogState) -> bool:
    if not state.expires_at:
        return False
    expires = state.expires_at if state.expires_at.tzinfo else state.expires_at.replace(tzinfo=timezone.utc)
    return expires < datetime.now(timezone.utc)


def _latest_dialog_state(db: Session, requester: Employee, conversation_key: str) -> DialogState | None:
    state = db.scalar(
        select(DialogState)
        .where(
            DialogState.requester_employee_id == requester.id,
            DialogState.conversation_id == conversation_key,
            DialogState.status == DialogStateStatus.active.value,
        )
        .order_by(DialogState.created_at.desc(), DialogState.id.desc())
    )
    if state and _state_expired(state):
        state.status = DialogStateStatus.expired.value
        state.resolved_at = datetime.now(timezone.utc)
        return None
    return state


def _clear_active_states(db: Session, requester: Employee, conversation_key: str, status: str = DialogStateStatus.cancelled.value) -> None:
    rows = db.scalars(
        select(DialogState).where(
            DialogState.requester_employee_id == requester.id,
            DialogState.conversation_id == conversation_key,
            DialogState.status == DialogStateStatus.active.value,
        )
    ).all()
    for row in rows:
        row.status = status
        row.resolved_at = datetime.now(timezone.utc)


def _create_dialog_state(db: Session, requester: Employee, conversation_key: str, state_type: str, payload: dict[str, Any]) -> DialogState:
    _clear_active_states(db, requester, conversation_key)
    state = DialogState(
        requester_employee_id=requester.id,
        conversation_id=conversation_key,
        state_type=state_type,
        payload=payload,
        status=DialogStateStatus.active.value,
        expires_at=_expire_time(),
    )
    db.add(state)
    db.flush()
    return state


def _resolve_state(state: DialogState, status: str = DialogStateStatus.resolved.value) -> None:
    state.status = status
    state.resolved_at = datetime.now(timezone.utc)


def _start_confirmation(db: Session, requester: Employee, conversation_key: str, action: PendingAction) -> AgentReply:
    _create_dialog_state(db, requester, conversation_key, 'confirm_pending_action', {'pending_action_id': action.id})
    return AgentReply(status='pending_confirmation', reply=_describe_pending_action(db, action, requester), pending_action_id=action.id)


def _describe_pending_action(db: Session, action: PendingAction, requester: Employee) -> str:
    payload = action.payload
    if action.action_type == 'create_event':
        participants = [db.get(Employee, eid) for eid in payload.get('participant_employee_ids', [])]
        participants = [p for p in participants if p]
        names = ', '.join([p.display_name or p.matrix_id for p in participants])
        reminder = payload.get('reminder_minutes')
        reminder_text = 'без напоминания' if reminder is None else f'напоминание за {reminder} мин.'
        return (
            f'Я понял так:\n\n'
            f'Действие: создать событие\n'
            f'Название: {payload.get("title")}\n'
            f'Участники: {names}\n'
            f'Время: {_format_interval(datetime.fromisoformat(payload["start"]), datetime.fromisoformat(payload["end"]), payload.get("timezone") or requester.timezone)}\n'
            f'{reminder_text}\n\n'
            f'Создать событие?'
        )
    if action.action_type == 'reschedule_event':
        meeting = db.get(Meeting, payload.get('meeting_id'))
        title = meeting.title if meeting else f'#{payload.get("meeting_id")}'
        return (
            f'Я понял так:\n\n'
            f'Действие: перенести событие “{title}”\n'
            f'Новое время: {_format_interval(datetime.fromisoformat(payload["new_start"]), datetime.fromisoformat(payload["new_end"]), requester.timezone)}\n\n'
            f'Перенести событие?'
        )
    if action.action_type == 'cancel_event':
        meeting = db.get(Meeting, payload.get('meeting_id'))
        title = meeting.title if meeting else f'#{payload.get("meeting_id")}'
        return f'Я понял так:\n\nДействие: отменить событие “{title}”.\n\nОтменить событие?'
    if action.action_type == 'slot_selection':
        return _format_slot_options(action.payload.get('slots', []), action.payload.get('timezone') or requester.timezone)
    return 'Есть незавершенное действие. Подтвердить?'


def _format_slot_options(slots: list[dict[str, str]], tz_name: str) -> str:
    lines = ['Нашел подходящие варианты:']
    for i, slot in enumerate(slots, start=1):
        lines.append(f'{i}. {_format_interval(datetime.fromisoformat(slot["start"]), datetime.fromisoformat(slot["end"]), tz_name)}')
    lines.append('\nКакой выбрать?')
    return '\n'.join(lines)


def _make_create_action_from_slot(db: Session, requester: Employee, slot_action: PendingAction, selected_index: int) -> PendingAction:
    payload = slot_action.payload
    slots = payload.get('slots', [])
    if selected_index < 1 or selected_index > len(slots):
        raise HTTPException(status_code=400, detail='Selected slot is out of range')
    slot = slots[selected_index - 1]
    create_payload = {
        'title': payload['title'],
        'description': payload.get('description'),
        'participant_employee_ids': payload['participant_employee_ids'],
        'start': slot['start'],
        'end': slot['end'],
        'timezone': payload.get('timezone') or requester.timezone,
        'reminder_minutes': payload.get('reminder_minutes'),
    }
    slot_action.status = PendingActionStatus.cancelled.value
    return create_pending_action(db, requester, 'create_event', create_payload)


def _create_slot_selection_action(db: Session, requester: Employee, intent: dict[str, Any]) -> PendingAction:
    refs = _participant_refs(intent)
    duration = int(intent.get('duration_minutes') or 30)
    start = _parse_dt(intent.get('date_range_start'))
    end = _parse_dt(intent.get('date_range_end'))
    slots = free_slots(db, requester.matrix_id, refs, duration, start, end, None)
    if not slots:
        raise HTTPException(status_code=404, detail='Свободные окна не найдены.')
    participants: list[Employee] = []
    for matrix_id in slots[0]['participants']:
        emp = ensure_employee(db, matrix_id)
        participants.append(emp)
    title = intent.get('title')
    if not title:
        others = [p.display_name or p.localpart for p in participants if p.id != requester.id]
        title = 'Личное событие' if not others else 'Встреча с ' + ', '.join(others[:2])
    reminder = None if intent.get('no_reminder') else (intent.get('reminder_minutes') if intent.get('reminder_minutes') is not None else get_settings().default_reminder_minutes)
    payload = {
        'title': title,
        'description': intent.get('description'),
        'participant_employee_ids': [p.id for p in participants],
        'timezone': requester.timezone,
        'reminder_minutes': reminder,
        'slots': [{'start': s['start'].isoformat(), 'end': s['end'].isoformat()} for s in slots],
    }
    return create_pending_action(db, requester, 'slot_selection', payload)


def _meeting_id_from_intent(intent: dict[str, Any]) -> int | None:
    value = intent.get('meeting_id')
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f'Invalid meeting_id: {value}') from exc


def _candidate_meetings(db: Session, requester: Employee, intent: dict[str, Any]) -> list[Meeting]:
    query = (intent.get('title') or intent.get('target_query') or '').strip().lower()
    start = _parse_dt(intent.get('date_range_start'))
    end = _parse_dt(intent.get('date_range_end'))
    if not start and not end:
        start, end = _local_date_range(requester, 'week')
    elif start and not end:
        end = start + timedelta(days=1)
    elif end and not start:
        start = datetime.now(timezone.utc)
    participant_ids: set[str] = set()
    for ref in _participant_refs(intent):
        try:
            if ref.matrix_id:
                participant_ids.add(ensure_employee(db, ref.matrix_id).id)
            elif ref.query:
                matches = find_employee(db, ref.query)
                if len(matches) == 1:
                    participant_ids.add(matches[0].id)
        except HTTPException:
            pass
    rows = db.scalars(
        select(Meeting)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .where(
            MeetingParticipant.employee_id == requester.id,
            Meeting.status == MeetingStatus.active.value,
            Meeting.end_time > _aware_utc(start),
            Meeting.start_time < _aware_utc(end),
        )
        .order_by(Meeting.start_time)
    ).all()
    result: list[Meeting] = []
    for meeting in rows:
        if query and query not in meeting.title.lower():
            if not participant_ids:
                continue
        if participant_ids:
            m_participants = set(
                db.scalars(select(MeetingParticipant.employee_id).where(MeetingParticipant.meeting_id == meeting.id)).all()
            )
            if not (participant_ids & m_participants):
                continue
        result.append(meeting)
    return result


def _format_schedule(events: list[dict], tz_name: str) -> str:
    if not events:
        return 'На выбранный период событий нет.'
    lines = ['Расписание:']
    for event in events:
        participants = ', '.join(event.get('participants') or [])
        suffix = f' — {participants}' if participants else ''
        lines.append(f'- {_format_interval(event["start"], event["end"], tz_name)} — {event["title"]}{suffix}')
    return '\n'.join(lines)


def _format_event_options(candidates: list[Meeting], tz_name: str, verb: str) -> str:
    lines = [f'Нашел несколько событий. Какое {verb}?']
    for i, meeting in enumerate(candidates[:10], start=1):
        lines.append(f'{i}. {_format_interval(meeting.start_time, meeting.end_time, tz_name)} — {meeting.title} (ID {meeting.id})')
    lines.append('\nОтветьте номером варианта.')
    return '\n'.join(lines)


def _format_employee_options(options: list[dict[str, Any]]) -> str:
    lines = ['Нашел несколько сотрудников. Кого выбрать?']
    for i, item in enumerate(options[:10], start=1):
        name = item.get('display_name') or item.get('id')
        lines.append(f'{i}. {name} — {item.get("matrix_id")}')
    lines.append('\nОтветьте номером варианта.')
    return '\n'.join(lines)


def _choice_options_from_employees(employees: list[Employee]) -> list[dict[str, Any]]:
    return [{'id': e.id, 'matrix_id': e.matrix_id, 'display_name': e.display_name} for e in employees]


def _replace_query_with_matrix_id(intent: dict[str, Any], field: str, query: str, matrix_id: str) -> dict[str, Any]:
    updated = _clone_intent(intent)
    if field == 'target_query':
        updated['target_query'] = matrix_id
        return updated
    queries = list(updated.get('participant_queries') or [])
    for i, value in enumerate(queries):
        if str(value).strip().lower() == str(query).strip().lower():
            del queries[i]
            break
    updated['participant_queries'] = queries
    matrix_ids = list(updated.get('participant_matrix_ids') or [])
    if matrix_id not in matrix_ids:
        matrix_ids.append(matrix_id)
    updated['participant_matrix_ids'] = matrix_ids
    return updated


def _maybe_create_employee_choice_state(db: Session, requester: Employee, conversation_key: str, intent: dict[str, Any]) -> AgentReply | None:
    intent_name = (intent.get('intent') or 'unknown').strip()
    target_query = intent.get('target_query')
    if intent_name == 'get_schedule' and target_query and not str(target_query).startswith('@'):
        matches = find_employee(db, str(target_query))
        if len(matches) > 1:
            options = _choice_options_from_employees(matches)
            _create_dialog_state(db, requester, conversation_key, 'choose_employee', {'intent': intent, 'field': 'target_query', 'query': str(target_query), 'options': options})
            return AgentReply(status='needs_clarification', reply=_format_employee_options(options))
        if len(matches) == 1:
            intent['target_query'] = matches[0].matrix_id
    if intent_name in {'find_free_slots', 'create_event', 'reschedule_event', 'cancel_event'}:
        for query in list(intent.get('participant_queries') or []):
            matches = find_employee(db, str(query))
            if len(matches) > 1:
                options = _choice_options_from_employees(matches)
                _create_dialog_state(db, requester, conversation_key, 'choose_employee', {'intent': intent, 'field': 'participant_queries', 'query': str(query), 'options': options})
                return AgentReply(status='needs_clarification', reply=_format_employee_options(options))
            if len(matches) == 1:
                updated = _replace_query_with_matrix_id(intent, 'participant_queries', str(query), matches[0].matrix_id)
                intent.clear()
                intent.update(updated)
    return None


def _start_slot_selection(db: Session, requester: Employee, conversation_key: str, intent: dict[str, Any]) -> AgentReply:
    action = _create_slot_selection_action(db, requester, intent)
    _create_dialog_state(db, requester, conversation_key, 'slot_selection', {'pending_action_id': action.id})
    return AgentReply(status='slot_selection', reply=_describe_pending_action(db, action, requester), pending_action_id=action.id)


def _start_event_choice(db: Session, requester: Employee, conversation_key: str, intent: dict[str, Any], candidates: list[Meeting], verb: str) -> AgentReply:
    options = [{'meeting_id': m.id, 'title': m.title, 'start': _aware_utc(m.start_time).isoformat(), 'end': _aware_utc(m.end_time).isoformat()} for m in candidates[:10]]
    _create_dialog_state(db, requester, conversation_key, 'choose_event', {'intent': intent, 'options': options})
    return AgentReply(status='needs_clarification', reply=_format_event_options(candidates, requester.timezone, verb))


def _start_await_reschedule_time(db: Session, requester: Employee, conversation_key: str, meeting_id: int) -> AgentReply:
    meeting = db.get(Meeting, meeting_id)
    title = meeting.title if meeting else f'#{meeting_id}'
    _create_dialog_state(db, requester, conversation_key, 'await_reschedule_time', {'meeting_id': meeting_id})
    return AgentReply(status='needs_clarification', reply=f'На какое время перенести событие “{title}”?')


def _handle_confirmation_state(db: Session, requester: Employee, matrix_id: str, state: DialogState, text: str) -> AgentReply:
    pending_action_id = state.payload.get('pending_action_id')
    if _is_negative(text):
        status, reply, meeting_id = confirm_pending_action(db, matrix_id, pending_action_id, False)
        _resolve_state(state, DialogStateStatus.cancelled.value)
        return AgentReply(status=status, reply=reply, pending_action_id=pending_action_id, meeting_id=meeting_id)
    if _is_affirmative(text):
        status, reply, meeting_id = confirm_pending_action(db, matrix_id, pending_action_id, True)
        _resolve_state(state, DialogStateStatus.resolved.value)
        return AgentReply(status=status, reply=reply, pending_action_id=pending_action_id, meeting_id=meeting_id)
    action = db.get(PendingAction, pending_action_id)
    prompt = _describe_pending_action(db, action, requester) if action else 'Подтвердить действие?'
    return AgentReply(status='awaiting_confirmation', reply=prompt + '\n\nОтветьте “да” или “нет”.', pending_action_id=pending_action_id)


def _handle_slot_state(db: Session, requester: Employee, state: DialogState, text: str) -> AgentReply:
    action = db.get(PendingAction, state.payload.get('pending_action_id'))
    if not action or action.status != PendingActionStatus.pending.value:
        _resolve_state(state, DialogStateStatus.expired.value)
        return AgentReply(status='expired', reply='Выбор слота больше не актуален. Повторите команду.')
    if _is_negative(text):
        action.status = PendingActionStatus.cancelled.value
        _resolve_state(state, DialogStateStatus.cancelled.value)
        return AgentReply(status='cancelled', reply='Действие отменено.', pending_action_id=action.id)
    selection = _selection_number(text)
    if _is_affirmative(text) and selection is None:
        selection = 1
    if selection is None:
        return AgentReply(status='awaiting_selection', reply=_describe_pending_action(db, action, requester) + '\n\nОтветьте номером варианта или “нет”.', pending_action_id=action.id)
    try:
        create_action = _make_create_action_from_slot(db, requester, action, selection)
    except HTTPException:
        return AgentReply(status='awaiting_selection', reply=_describe_pending_action(db, action, requester) + '\n\nТакого варианта нет. Ответьте номером из списка.')
    _resolve_state(state, DialogStateStatus.resolved.value)
    return _start_confirmation(db, requester, state.conversation_id, create_action)


def _handle_employee_choice_state(db: Session, requester: Employee, matrix_id: str, state: DialogState, text: str) -> AgentReply:
    if _is_negative(text):
        _resolve_state(state, DialogStateStatus.cancelled.value)
        return AgentReply(status='cancelled', reply='Действие отменено.')
    selection = _selection_number(text)
    options = state.payload.get('options') or []
    if selection is None or selection < 1 or selection > len(options):
        return AgentReply(status='awaiting_selection', reply=_format_employee_options(options))
    selected = options[selection - 1]
    intent = _replace_query_with_matrix_id(state.payload.get('intent') or {}, state.payload.get('field'), state.payload.get('query'), selected['matrix_id'])
    _resolve_state(state, DialogStateStatus.resolved.value)
    return _handle_intent(db, requester, matrix_id, state.conversation_id, intent)


def _handle_event_choice_state(db: Session, requester: Employee, matrix_id: str, state: DialogState, text: str) -> AgentReply:
    if _is_negative(text):
        _resolve_state(state, DialogStateStatus.cancelled.value)
        return AgentReply(status='cancelled', reply='Действие отменено.')
    selection = _selection_number(text)
    options = state.payload.get('options') or []
    if selection is None or selection < 1 or selection > len(options):
        # Rebuild a lightweight answer from stored options.
        lines = ['Какое событие выбрать?']
        for i, item in enumerate(options, start=1):
            lines.append(f'{i}. {_format_interval(datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]), requester.timezone)} — {item["title"]} (ID {item["meeting_id"]})')
        lines.append('\nОтветьте номером варианта или “нет”.')
        return AgentReply(status='awaiting_selection', reply='\n'.join(lines))
    selected = options[selection - 1]
    intent = _clone_intent(state.payload.get('intent') or {})
    intent['meeting_id'] = selected['meeting_id']
    _resolve_state(state, DialogStateStatus.resolved.value)
    return _handle_intent(db, requester, matrix_id, state.conversation_id, intent)


def _handle_await_reschedule_time_state(db: Session, requester: Employee, matrix_id: str, state: DialogState, text: str) -> AgentReply:
    if _is_negative(text):
        _resolve_state(state, DialogStateStatus.cancelled.value)
        return AgentReply(status='cancelled', reply='Действие отменено.')
    intent = _call_llm(text, requester)
    new_start = _parse_dt(intent.get('new_start') or intent.get('start'))
    new_end = _parse_dt(intent.get('new_end') or intent.get('end'))
    if not new_start:
        return AgentReply(status='needs_clarification', reply='Не понял новое время. Напишите, например: “завтра в 16:00”.')
    if not new_end:
        meeting = db.get(Meeting, state.payload.get('meeting_id'))
        duration = (meeting.end_time - meeting.start_time) if meeting else timedelta(minutes=int(intent.get('duration_minutes') or 30))
        new_end = new_start + duration
    action = draft_reschedule_event(db, matrix_id, int(state.payload['meeting_id']), new_start, new_end)
    _resolve_state(state, DialogStateStatus.resolved.value)
    return _start_confirmation(db, requester, state.conversation_id, action)


def _handle_active_state(db: Session, requester: Employee, matrix_id: str, state: DialogState, text: str) -> AgentReply:
    if state.state_type == 'confirm_pending_action':
        return _handle_confirmation_state(db, requester, matrix_id, state, text)
    if state.state_type == 'slot_selection':
        return _handle_slot_state(db, requester, state, text)
    if state.state_type == 'choose_employee':
        return _handle_employee_choice_state(db, requester, matrix_id, state, text)
    if state.state_type == 'choose_event':
        return _handle_event_choice_state(db, requester, matrix_id, state, text)
    if state.state_type == 'await_reschedule_time':
        return _handle_await_reschedule_time_state(db, requester, matrix_id, state, text)
    _resolve_state(state, DialogStateStatus.cancelled.value)
    return AgentReply(status='error', reply='Диалоговое состояние устарело. Повторите команду.')


def _handle_intent(db: Session, requester: Employee, matrix_id: str, conversation_key: str, intent: dict[str, Any]) -> AgentReply:
    intent = _clone_intent(intent)
    intent_name = (intent.get('intent') or 'unknown').strip()

    choice = _maybe_create_employee_choice_state(db, requester, conversation_key, intent)
    if choice:
        return choice

    if intent_name == 'unknown':
        return AgentReply(status='needs_clarification', reply=intent.get('reply') or 'Не понял команду. Уточните, что нужно сделать с календарем.')

    if intent_name == 'search_employees':
        query = intent.get('target_query') or ''
        matches = find_employee(db, str(query)) if query else []
        if not matches:
            return AgentReply(status='not_found', reply='Сотрудники не найдены.')
        lines = ['Нашел сотрудников:']
        for emp in matches[:10]:
            lines.append(f'- {emp.display_name or emp.localpart} — {emp.matrix_id}')
        return AgentReply(status='ok', reply='\n'.join(lines), employees=[e.matrix_id for e in matches[:10]])

    if intent_name == 'get_schedule':
        start = _parse_dt(intent.get('date_range_start'))
        end = _parse_dt(intent.get('date_range_end'))
        if not start or not end:
            start, end = _local_date_range(requester, 'today')
        target_query = intent.get('target_query')
        target_matrix_id = None
        if target_query:
            matches = find_employee(db, str(target_query))
            if len(matches) == 1:
                target_matrix_id = matches[0].matrix_id
            elif len(matches) > 1:
                options = _choice_options_from_employees(matches)
                _create_dialog_state(db, requester, conversation_key, 'choose_employee', {'intent': intent, 'field': 'target_query', 'query': str(target_query), 'options': options})
                return AgentReply(status='needs_clarification', reply=_format_employee_options(options))
            else:
                return AgentReply(status='not_found', reply='Сотрудник не найден.')
        events = get_schedule(db, matrix_id, target_matrix_id, start, end)
        return AgentReply(status='ok', reply=_format_schedule(events, requester.timezone), events=events)

    if intent_name == 'find_free_slots':
        return _start_slot_selection(db, requester, conversation_key, intent)

    if intent_name == 'create_event':
        if intent.get('needs_free_slot_search') or not intent.get('start') or not intent.get('end'):
            return _start_slot_selection(db, requester, conversation_key, intent)
        action = draft_create_event(
            db,
            matrix_id,
            intent.get('title'),
            intent.get('description'),
            _participant_refs(intent),
            _parse_dt(intent.get('start')),
            _parse_dt(intent.get('end')),
            None,
            intent.get('reminder_minutes'),
            bool(intent.get('no_reminder')),
        )
        return _start_confirmation(db, requester, conversation_key, action)

    if intent_name == 'reschedule_event':
        meeting_id = _meeting_id_from_intent(intent)
        if not meeting_id:
            candidates = _candidate_meetings(db, requester, intent)
            if len(candidates) == 0:
                return AgentReply(status='not_found', reply='Не нашел подходящее событие для переноса. Уточните дату, участника или ID события.')
            if len(candidates) > 1:
                return _start_event_choice(db, requester, conversation_key, intent, candidates, 'перенести')
            meeting_id = candidates[0].id
        new_start = _parse_dt(intent.get('new_start') or intent.get('start'))
        new_end = _parse_dt(intent.get('new_end') or intent.get('end'))
        if not new_start:
            return _start_await_reschedule_time(db, requester, conversation_key, meeting_id)
        if not new_end:
            meeting = db.get(Meeting, meeting_id)
            if meeting:
                new_end = new_start + (meeting.end_time - meeting.start_time)
            else:
                duration = int(intent.get('duration_minutes') or 30)
                new_end = new_start + timedelta(minutes=duration)
        action = draft_reschedule_event(db, matrix_id, meeting_id, new_start, new_end)
        return _start_confirmation(db, requester, conversation_key, action)

    if intent_name == 'cancel_event':
        meeting_id = _meeting_id_from_intent(intent)
        if not meeting_id:
            candidates = _candidate_meetings(db, requester, intent)
            if len(candidates) == 0:
                return AgentReply(status='not_found', reply='Не нашел подходящее событие для отмены. Уточните дату, участника или ID события.')
            if len(candidates) > 1:
                return _start_event_choice(db, requester, conversation_key, intent, candidates, 'отменить')
            meeting_id = candidates[0].id
        action = draft_cancel_event(db, matrix_id, meeting_id)
        return _start_confirmation(db, requester, conversation_key, action)

    return AgentReply(status='needs_clarification', reply='Команда распознана, но пока не поддерживается в agent runtime.')


def process_agent_message(db: Session, matrix_id: str, message: str, display_name: str | None = None, email: str | None = None, conversation_id: str | None = None) -> AgentReply:
    requester = ensure_employee(db, matrix_id, display_name, email)
    text = message.strip()
    conversation_key = _conversation_key(conversation_id)

    state = _latest_dialog_state(db, requester, conversation_key)
    if state:
        return _handle_active_state(db, requester, matrix_id, state, text)

    intent = _call_llm(text, requester)
    try:
        return _handle_intent(db, requester, matrix_id, conversation_key, intent)
    except HTTPException as exc:
        # Convert expected tool errors into user-friendly conversational replies.
        detail = exc.detail
        if isinstance(detail, dict) and detail.get('matches'):
            options = detail.get('matches') or []
            _create_dialog_state(db, requester, conversation_key, 'choose_employee', {'intent': intent, 'field': 'participant_queries', 'query': '', 'options': options})
            return AgentReply(status='needs_clarification', reply=_format_employee_options(options))
        return AgentReply(status='error', reply=str(detail))
