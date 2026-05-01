from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_VALUES = {
    '',
    '***',
    'change-me',
    'replace-with-random-agent-token',
    'replace-with-random-admin-token',
    'replace-with-random-radicale-password',
    'replac...oken',
    'replac...ken',
}
MIN_TOKEN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    app_name: str = 'calendar-agent'
    app_env: str = 'dev'
    app_host: str = '0.0.0.0'
    app_port: int = 8080

    database_url: str = 'sqlite:////app/data/app.db'

    agent_api_token: str = 'replace-with-random-agent-token'
    admin_api_token: str = 'replace-with-random-admin-token'

    auto_provision_employees: bool = True
    allowed_matrix_servers: str = 'org1.company.ru'
    disable_unknown_homeservers: bool = True
    blocked_matrix_users: str = ''

    default_timezone: str = 'Europe/Moscow'
    default_workday_start: str = '09:00'
    default_workday_end: str = '18:00'
    default_workdays: str = 'MO,TU,WE,TH,FR'
    default_reminder_minutes: int = 15
    default_freebusy_workdays: int = 5
    max_free_slots: int = 3

    radicale_url: str = 'http://radicale:5232'
    radicale_username: str = 'calendar-service'
    radicale_password: str = 'change-me'
    radicale_calendar_root: str = '/calendars'
    radicale_strict_writes: bool = True

    mcp_enabled: bool = True
    mcp_transport: str = 'stdio'

    @property
    def allowed_servers_list(self) -> List[str]:
        return [x.strip().lower() for x in self.allowed_matrix_servers.split(',') if x.strip()]

    @property
    def blocked_users_list(self) -> List[str]:
        return [x.strip() for x in self.blocked_matrix_users.split(',') if x.strip()]

    @property
    def workdays_list(self) -> List[str]:
        return [x.strip().upper() for x in self.default_workdays.split(',') if x.strip()]


def _validate_secret(name: str, value: str, *, min_length: int = MIN_TOKEN_LENGTH) -> None:
    normalized = value.strip()
    if normalized != value or normalized in PLACEHOLDER_VALUES or len(normalized) < min_length:
        raise RuntimeError(f'{name} must be set to a non-placeholder random value with at least {min_length} characters')
    try:
        normalized.encode('ascii')
    except UnicodeEncodeError as exc:
        raise RuntimeError(f'{name} must contain only ASCII characters') from exc


def validate_runtime_settings(settings: Settings | None = None) -> None:
    """Fail fast for unsafe runtime secrets.

    Settings construction itself stays side-effect free so unit tests and CLI
    helpers can instantiate Settings. The FastAPI startup path calls this
    validator before serving requests.
    """
    settings = settings or get_settings()
    _validate_secret('AGENT_API_TOKEN', settings.agent_api_token)
    _validate_secret('ADMIN_API_TOKEN', settings.admin_api_token)
    if settings.agent_api_token == settings.admin_api_token:
        raise RuntimeError('AGENT_API_TOKEN and ADMIN_API_TOKEN must be different')
    if settings.radicale_strict_writes:
        _validate_secret('RADICALE_PASSWORD', settings.radicale_password, min_length=12)


@lru_cache
def get_settings() -> Settings:
    return Settings()
