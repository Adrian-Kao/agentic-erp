from typing import Annotated, cast

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader

from erp_agent.application.services import Services
from erp_agent.security.authorization import Principal

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


ServiceDep = Annotated[Services, Depends(get_services)]
KeyDep = Annotated[str | None, Security(api_key_header)]


def authenticate(services: ServiceDep, key: KeyDep) -> Principal:
    return services.auth.authenticate(key)


PrincipalDep = Annotated[Principal, Depends(authenticate)]
