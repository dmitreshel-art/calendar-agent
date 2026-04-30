from __future__ import annotations
import html
import uuid
from datetime import datetime
from icalendar import Calendar, Event
from dateutil.tz import gettz
import requests
from requests.auth import HTTPBasicAuth
from .config import get_settings


class RadicaleClient:
    """Small CalDAV adapter.

    SQLite is the authoritative store for logical meetings. Radicale receives
    event copies so CalDAV clients can read calendars if needed.

    The adapter uses direct WebDAV/CalDAV requests for calendar collections,
    because a service account writes to per-employee calendar paths rather than
    to its own principal calendar.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.settings.radicale_username, self.settings.radicale_password)

    def _url(self, path: str) -> str:
        return self.settings.radicale_url.rstrip('/') + '/' + path.strip('/')

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        headers = kwargs.pop('headers', {})
        return requests.request(
            method,
            self._url(path),
            auth=self.auth,
            headers=headers,
            timeout=10,
            **kwargs,
        )

    def ensure_calendar(self, calendar_path: str, display_name: str | None = None) -> None:
        """Best-effort creation of the employee calendar collection.

        For a target path like /calendars/org/user/default:
        - create intermediate WebDAV collections with MKCOL;
        - create the final CalDAV collection with MKCALENDAR.
        """
        parts = [p for p in calendar_path.strip('/').split('/') if p]
        if not parts:
            return
        try:
            current = ''
            for part in parts[:-1]:
                current = f'{current}/{part}' if current else part
                response = self._request('MKCOL', current)
                if response.status_code not in (201, 405, 409):
                    if self.settings.radicale_strict_writes:
                        response.raise_for_status()

            final_path = '/'.join(parts)
            safe_name = html.escape(display_name or (parts[-2] if len(parts) > 1 else parts[-1]))
            body = f'''<?xml version="1.0" encoding="utf-8" ?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set>
    <D:prop>
      <D:displayname>{safe_name}</D:displayname>
    </D:prop>
  </D:set>
</C:mkcalendar>'''
            response = self._request(
                'MKCALENDAR',
                final_path,
                headers={'Content-Type': 'application/xml; charset=utf-8'},
                data=body.encode('utf-8'),
            )
            if response.status_code not in (201, 405):
                if self.settings.radicale_strict_writes:
                    response.raise_for_status()
        except Exception:
            if self.settings.radicale_strict_writes:
                raise
            return

    @staticmethod
    def build_ics(uid: str, title: str, start: datetime, end: datetime, timezone: str, description: str | None = None) -> str:
        cal = Calendar()
        cal.add('prodid', '-//calendar-agent//calendar-service//EN')
        cal.add('version', '2.0')
        event = Event()
        event.add('uid', uid)
        event.add('summary', title)
        if description:
            event.add('description', description)
        tz = gettz(timezone)
        event.add('dtstart', start.astimezone(tz) if start.tzinfo and tz else start)
        event.add('dtend', end.astimezone(tz) if end.tzinfo and tz else end)
        event.add('dtstamp', datetime.utcnow())
        cal.add_component(event)
        return cal.to_ical().decode('utf-8')

    def create_or_update_event(self, calendar_path: str, uid: str, title: str, start: datetime, end: datetime, timezone: str, description: str | None = None) -> str:
        self.ensure_calendar(calendar_path)
        ics = self.build_ics(uid, title, start, end, timezone, description)
        event_path = calendar_path.rstrip('/') + f'/{uid}.ics'
        try:
            response = self._request('PUT', event_path, headers={'Content-Type': 'text/calendar; charset=utf-8'}, data=ics.encode('utf-8'))
            if response.status_code not in (200, 201, 204):
                if self.settings.radicale_strict_writes:
                    response.raise_for_status()
        except Exception:
            if self.settings.radicale_strict_writes:
                raise
        return uid

    def delete_event(self, calendar_path: str, uid: str) -> None:
        event_path = calendar_path.rstrip('/') + f'/{uid}.ics'
        try:
            response = self._request('DELETE', event_path)
            if response.status_code not in (200, 204, 404):
                if self.settings.radicale_strict_writes:
                    response.raise_for_status()
        except Exception:
            if self.settings.radicale_strict_writes:
                raise
            return


def new_uid() -> str:
    return f'{uuid.uuid4()}@calendar-agent'
