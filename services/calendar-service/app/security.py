from fastapi import Depends, Header, HTTPException, status
from .config import get_settings


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')
    return authorization.split(' ', 1)[1].strip()


def require_agent_token(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    token = _extract_bearer(authorization)
    if token != settings.agent_api_token and token != settings.admin_api_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid agent token')


def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    token = _extract_bearer(authorization)
    if token != settings.admin_api_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid admin token')
