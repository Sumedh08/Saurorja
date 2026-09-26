from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise RuntimeError("validation error handler received an unexpected exception")
    del exc
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(
                code="validation_error",
                message="The request could not be validated",
                request_id=getattr(request.state, "request_id", "-"),
            )
        ).model_dump(),
    )


async def http_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        raise RuntimeError("HTTP error handler received an unexpected exception")
    status_codes = {
        404: ("not_found", "The requested resource was not found"),
        405: ("method_not_allowed", "The method is not allowed"),
    }
    detail: dict[str, object] = exc.detail if isinstance(exc.detail, dict) else {}
    code, message = status_codes.get(
        exc.status_code, ("request_error", "The request could not be completed")
    )
    detail_code = detail.get("code")
    detail_message = detail.get("message")
    if isinstance(detail_code, str) and isinstance(detail_message, str):
        code, message = detail_code, detail_message
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=code, message=message, request_id=getattr(request.state, "request_id", "-")
            )
        ).model_dump(),
    )
