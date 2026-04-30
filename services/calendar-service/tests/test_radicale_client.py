from types import SimpleNamespace

from app.radicale_client import RadicaleClient


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.raised = False

    def raise_for_status(self) -> None:
        self.raised = True
        raise AssertionError(f"raise_for_status called for {self.status_code}")


def test_ensure_calendar_treats_existing_mkcalendar_as_success():
    client = RadicaleClient()
    client.settings = SimpleNamespace(radicale_strict_writes=True)
    calls: list[tuple[str, str]] = []
    responses = iter([
        FakeResponse(405),  # /calendars already exists
        FakeResponse(405),  # /calendars/org already exists
        FakeResponse(405),  # /calendars/org/user already exists
        FakeResponse(409),  # final calendar collection already exists
    ])

    def fake_request(method: str, path: str, **kwargs):
        calls.append((method, path))
        return next(responses)

    client._request = fake_request  # type: ignore[method-assign]

    client.ensure_calendar('/calendars/org1.company.ru/ivanov/default', 'Иванов Иван')

    assert calls == [
        ('MKCOL', 'calendars'),
        ('MKCOL', 'calendars/org1.company.ru'),
        ('MKCOL', 'calendars/org1.company.ru/ivanov'),
        ('MKCALENDAR', 'calendars/org1.company.ru/ivanov/default'),
    ]
