from functools import lru_cache
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    llm_base_url: str = 'https://api.example.ru/v1'
    llm_api_key: str = 'change-me'
    llm_model: str = 'qwen-plus'
    llm_timeout_seconds: int = 30

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
