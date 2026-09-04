"""RFC 9457 error responses shared by HTTP routes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from seokpan.identity.application import IdentityRuleViolation, SessionRuleViolation
from seokpan.identity.application.auth_session import SessionTransitionUnavailable

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass(frozen=True, slots=True)
class ApiProblem(Exception):
    status: int
    code: str
    title: str


class ProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    code: str
    request_id: str
    errors: list[dict[str, str]] | None = None


_IDENTITY_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication failed or is required"},
    403: {"description": "Origin or CSRF validation failed"},
    409: {"description": "Login ID or nickname conflict"},
    422: {"description": "Request validation failed"},
    503: {"description": "Identity or Session service unavailable"},
}
for _problem_response in _IDENTITY_ERROR_RESPONSES.values():
    _problem_response["content"] = {
        "application/problem+json": {"schema": ProblemResponse.model_json_schema()}
    }


def identity_problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: _IDENTITY_ERROR_RESPONSES[code] for code in codes}


def request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "")
    if _REQUEST_ID.fullmatch(supplied) is not None:
        return supplied
    return str(uuid4())


def install_problem_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiProblem)
    async def api_problem_handler(request: Request, error: ApiProblem) -> JSONResponse:
        return _response(request, error.status, error.code, error.title)

    @application.exception_handler(IdentityRuleViolation)
    async def identity_problem_handler(
        request: Request,
        error: IdentityRuleViolation,
    ) -> JSONResponse:
        status, title = _identity_status(error.code)
        return _response(request, status, error.code, title)

    @application.exception_handler(SessionRuleViolation)
    async def session_problem_handler(
        request: Request,
        error: SessionRuleViolation,
    ) -> JSONResponse:
        status = 401 if error.code == "SESSION_NOT_FOUND" else 503
        title = "Authentication required" if status == 401 else "Session unavailable"
        return _response(request, status, error.code, title)

    @application.exception_handler(SessionTransitionUnavailable)
    async def transition_problem_handler(
        request: Request,
        _error: SessionTransitionUnavailable,
    ) -> JSONResponse:
        return _response(request, 503, "SESSION_TRANSITION_UNAVAILABLE", "Session unavailable")

    @application.exception_handler(RequestValidationError)
    async def validation_problem_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        fields = [
            {"location": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
            for item in error.errors()
        ]
        return _response(
            request,
            422,
            "VALIDATION_FAILED",
            "Request validation failed",
            errors=fields,
        )


def _identity_status(code: str) -> tuple[int, str]:
    if code == "AUTH_INVALID_CREDENTIALS":
        return 401, "Invalid credentials"
    if code in {"LOGIN_ID_ALREADY_EXISTS", "NICKNAME_ALREADY_EXISTS"}:
        return 409, "Member already exists"
    if "PROVIDER" in code or code in {"IDENTITY_COMMIT_UNCERTAIN", "PASSWORD_HASH_UNAVAILABLE"}:
        return 503, "Identity service unavailable"
    return 422, "Request validation failed"


def _response(
    request: Request,
    status: int,
    code: str,
    title: str,
    *,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"urn:seokpan:problem:{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": request_id(request),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json")
