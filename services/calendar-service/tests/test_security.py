from types import SimpleNamespace

from fastapi import HTTPException

import app.security as security
from app.security import require_admin_token, require_agent_token


SAFE_AGENT_TOKEN = 'agent-' + 'a' * 40
SAFE_ADMIN_TOKEN = 'admin-' + 'b' * 40


def test_agent_token_dependency_accepts_configured_agent_token(monkeypatch):
    monkeypatch.setattr(security, 'get_settings', lambda: SimpleNamespace(agent_api_token=SAFE_AGENT_TOKEN, admin_api_token=SAFE_ADMIN_TOKEN))

    require_agent_token(f'Bearer {SAFE_AGENT_TOKEN}')


def test_admin_token_dependency_accepts_configured_admin_token(monkeypatch):
    monkeypatch.setattr(security, 'get_settings', lambda: SimpleNamespace(agent_api_token=SAFE_AGENT_TOKEN, admin_api_token=SAFE_ADMIN_TOKEN))

    require_admin_token(f'Bearer {SAFE_ADMIN_TOKEN}')


def test_agent_token_dependency_rejects_non_ascii_bearer_with_403():
    try:
        require_agent_token('Bearer токен')
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover
        raise AssertionError('expected HTTPException')


def test_admin_token_dependency_rejects_non_ascii_bearer_with_403():
    try:
        require_admin_token('Bearer токен')
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover
        raise AssertionError('expected HTTPException')
