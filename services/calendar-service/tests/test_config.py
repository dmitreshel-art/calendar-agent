import pytest

from app.config import Settings, validate_runtime_settings


def make_settings(**overrides) -> Settings:
    data = {
        'agent_api_token': 'agent-' + 'a' * 40,
        'admin_api_token': 'admin-' + 'b' * 40,
        'radicale_password': 'radicale-' + 'c' * 32,
    }
    data.update(overrides)
    return Settings(**data)


@pytest.mark.parametrize('token', [
    'replace-with-random-agent-token',
    ' replace-with-random-agent-token ',
    'replace-with-random-admin-token',
    'replac...oken',
    '***',
    'short',
])
def test_runtime_settings_reject_placeholder_or_short_agent_token(token):
    with pytest.raises(RuntimeError, match='AGENT_API_TOKEN'):
        validate_runtime_settings(make_settings(agent_api_token=token))


@pytest.mark.parametrize('token', [
    'replace-with-random-admin-token',
    ' replace-with-random-admin-token ',
    '***',
    'short',
])
def test_runtime_settings_reject_placeholder_or_short_admin_token(token):
    with pytest.raises(RuntimeError, match='ADMIN_API_TOKEN'):
        validate_runtime_settings(make_settings(admin_api_token=token))


def test_runtime_settings_reject_short_radicale_password_when_strict_writes_enabled():
    with pytest.raises(RuntimeError, match='RADICALE_PASSWORD'):
        validate_runtime_settings(make_settings(radicale_password='short'))


def test_runtime_settings_reject_non_ascii_secret():
    with pytest.raises(RuntimeError, match='AGENT_API_TOKEN'):
        validate_runtime_settings(make_settings(agent_api_token='agent-' + 'я' * 40))


def test_runtime_settings_skips_radicale_password_validation_when_strict_writes_disabled():
    validate_runtime_settings(make_settings(radicale_password='short', radicale_strict_writes=False))


def test_runtime_settings_reject_same_agent_and_admin_token():
    shared = 'shared-' + 'x' * 40
    with pytest.raises(RuntimeError, match='must be different'):
        validate_runtime_settings(make_settings(agent_api_token=shared, admin_api_token=shared))


def test_runtime_settings_accept_long_distinct_tokens():
    validate_runtime_settings(make_settings())
