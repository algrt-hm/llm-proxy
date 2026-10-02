"""The proxy's own error envelope, shared by every route."""

from typing import Any

from fastapi.responses import JSONResponse


def error_detail(message: str, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "llmproxy_error",
            "request_id": request_id,
        }
    }


def error_response(
    status_code: int,
    message: str,
    request_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = {"x-request-id": request_id}
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        status_code=status_code,
        content=error_detail(message, request_id),
        headers=headers,
    )
