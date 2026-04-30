from datetime import datetime, timedelta, timezone
import sys
from .database import init_db, session_scope
from .employees import ensure_employee
from .calendar_logic import draft_create_event, confirm_pending_action
from .schemas import ParticipantRef


def demo_seed() -> None:
    init_db()
    with session_scope() as db:
        ivanov = ensure_employee(db, '@ivanov:org1.company.ru', 'Иванов Иван')
        petrova = ensure_employee(db, '@petrova:org1.company.ru', 'Петрова Анна')
        sidorov = ensure_employee(db, '@sidorov:org2.company.ru', 'Сидоров Петр')
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
        action = draft_create_event(
            db,
            ivanov.matrix_id,
            'Демо-встреча по календарному агенту',
            'Тестовая встреча из demo-seed',
            [ParticipantRef(matrix_id=petrova.matrix_id), ParticipantRef(matrix_id=sidorov.matrix_id)],
            now + timedelta(hours=1),
            now + timedelta(hours=2),
            None,
            15,
            False,
        )
        confirm_pending_action(db, ivanov.matrix_id, action.id, True)
        personal = draft_create_event(
            db,
            petrova.matrix_id,
            'Личное демо-событие',
            None,
            [],
            now + timedelta(hours=3),
            now + timedelta(hours=4),
            None,
            None,
            False,
        )
        confirm_pending_action(db, petrova.matrix_id, personal.id, True)
    print('Demo data created.')


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'demo-seed':
        demo_seed()
    else:
        print('Usage: python -m app.cli demo-seed')
        sys.exit(1)


if __name__ == '__main__':
    main()
